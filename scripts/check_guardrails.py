"""Static checks for the non-negotiable rules in AGENTS.md.

    python scripts/check_guardrails.py

Each check prints PASS/FAIL. Exit code 1 if any FAIL. Warnings (e.g. rules not yet
verified) are printed as GitHub annotations but don't fail the build.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.prompts import TOOL_SCHEMAS  # noqa: E402
from app.agent.tools import ToolBox  # noqa: E402
from app.data.seed import build_branch_profiles, build_profiles  # noqa: E402
from app.privacy import FULL_AADHAAR, find_full_ids  # noqa: E402
from app.rules import engine  # noqa: E402

failures: list[str] = []
warnings: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


def py_files(*dirs: str) -> list[Path]:
    return [p for d in dirs for p in (ROOT / d).rglob("*.py")]


# Rule 1: the LLM never decides eligibility -------------------------------------------------

_STATUSES = "|".join(engine.ALL_STATUSES)
STATUS_LITERAL = re.compile(r"""[=!]=\s*["'](""" + _STATUSES + r""")["']""")
offenders = []
for f in py_files("app"):
    if f.name == "engine.py":
        continue
    for n, line in enumerate(f.read_text().splitlines(), 1):
        if STATUS_LITERAL.search(line):
            offenders.append(f"{f.relative_to(ROOT)}:{n}")
check("rule 1: status strings are compared via engine constants only", not offenders, ", ".join(offenders))

impure = []
for f in py_files("app/rules", "app/planner"):
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            if m.startswith(("app.agent", "anthropic", "openai", "sqlite3", "requests", "app.api", "app.db")):
                impure.append(f"{f.relative_to(ROOT)} imports {m}")
check("rule 1: rules/ and planner/ stay pure (no LLM, DB or network imports)", not impure, "; ".join(impure))


# Rule 2: nothing is submitted without officer approval ---------------------------------------

schema_names = {t["name"] for t in TOOL_SCHEMAS}
forbidden = {n for n in schema_names if re.search(r"approve|submit|status|eligib", n) and n != "check_eligibility"}
check("rule 2: no approve/submit/set-status tool is exposed to the LLM", not forbidden, str(forbidden))

class _Dummy:  # ToolBox needs a conn only to build its table
    pass
toolbox_names = set(ToolBox(_Dummy(), "x")._tools)  # type: ignore[arg-type]
check("rule 2: LLM tool schemas match ToolBox exactly", schema_names == toolbox_names,
      f"schemas only: {schema_names - toolbox_names}; toolbox only: {toolbox_names - schema_names}")

# Only the officer route may approve or reject. Look for any way to reach the officer-only queue
# transitions, not just `x.approve(...)`: an aliased import (`from app.queue import approve as done`),
# a bare reference (`f = queue.approve`), getattr with a string, or raw SQL on queue_items.
OFFICER_ONLY = {"approve", "reject", "_move"}
QUEUE_SQL = re.compile(r"\b(UPDATE|INSERT\s+INTO|REPLACE\s+INTO)\s+queue_items\b", re.IGNORECASE)
GATE_OWNERS = {"app/queue.py", "app/api/main.py"}  # the state machine and the officer routes


def _reaches_gate(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):  # from app.queue import approve [as anything]
        return node.module == "app.queue" and any(a.name in OFFICER_ONLY | {"*"} for a in node.names)
    if isinstance(node, ast.Attribute):  # queue.approve, called or not
        return node.attr in OFFICER_ONLY
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        return any(isinstance(a, ast.Constant) and a.value in OFFICER_ONLY for a in node.args)
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(QUEUE_SQL.search(node.value))


def approval_bypasses(source: str) -> list[int]:
    """Line numbers in `source` that reach an officer-only queue transition."""
    return sorted({n.lineno for n in ast.walk(ast.parse(source)) if _reaches_gate(n)})


# The check must catch the bypasses we know about, or it proves nothing.
_KNOWN_BYPASSES = [
    "queue.approve(conn, i, 'agent')",
    "from app.queue import approve as finish\nfinish(conn, i, 'agent')",
    "from app.queue import *",
    "f = queue.approve",
    "getattr(queue, 'approve')(conn, i, 'agent')",
    "queue._move(conn, item, 'SUBMITTED')",
    "conn.execute(\"UPDATE queue_items SET status = 'SUBMITTED'\")",
]
missed = [src for src in _KNOWN_BYPASSES if not approval_bypasses(src)]
check("rule 2: the approval-bypass detector catches every known bypass", not missed, str(missed))

approve_refs = [f"{f.relative_to(ROOT)}:{n}" for f in py_files("app", "ui")
                if str(f.relative_to(ROOT)) not in GATE_OWNERS
                for n in approval_bypasses(f.read_text())]
check("rule 2: only the officer routes reach queue approve/reject or write queue_items", not approve_refs,
      ", ".join(approve_refs))


# Rule 3: every decision cites a rule and a source ----------------------------------------------

try:
    catalog = engine.load_products()
    check("rule 3: products.json passes startup validation", True)
except engine.RulesValidationError as e:
    catalog = None
    check("rule 3: products.json passes startup validation", False, str(e))

if catalog:
    bad_src = [p["id"] for p in catalog["products"] if not str(p.get("source_url", "")).startswith("https://")]
    check("rule 3: every product cites an https source_url", not bad_src, str(bad_src))
    unverified = [p["id"] for p in catalog["products"]
                  if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p.get("last_verified", "")))]
    if unverified:
        warnings.append(f"last_verified is not a date for {', '.join(unverified)}: verify against the official "
                        "scheme pages (REFERENCE.md ⚠) before the demo")


# Rule 5: synthetic, masked data only ---------------------------------------------------------

SEEDED = build_profiles() + build_branch_profiles()  # demo personas + branch customers (C101-C130)
hits = []
for prof in SEEDED:
    hits += [f"seed {prof['customer_id']}.{h}" for h in find_full_ids(prof)]
scan = [*py_files("app", "ui", "scripts"), *ROOT.glob("app/**/*.json"), *ROOT.glob("docs/*.md"), ROOT / "README.md",
        ROOT / "REFERENCE.md"]
for f in scan:
    if f.name == "privacy.py":
        continue
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if FULL_AADHAAR.search(line):
            hits.append(f"{f.relative_to(ROOT)}:{n}")
check("rule 5: no full Aadhaar-like numbers in seed data, code or docs", not hits, ", ".join(hits[:10]))

masks = [p["customer_id"] for p in SEEDED
         if p.get("aadhaar_masked") and not re.fullmatch(r"XXXX-XXXX-\d{4}", p["aadhaar_masked"])]
check("rule 5: every seeded Aadhaar uses the XXXX-XXXX-1234 mask", not masks, str(masks))
masks = [f"{p['customer_id']}.{k}" for p in SEEDED for k in ("account_no_masked", "mobile_masked")
         if p.get(k) and not re.fullmatch(r"XXXXXX\d{4}", p[k])]
check("rule 5: every seeded account and mobile number uses the XXXXXX1234 mask", not masks, str(masks))


# Secrets ------------------------------------------------------------------------------------

tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
check(".env is not committed", ".env" not in tracked)
SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|"
                    r"(LLM_API_KEY|SARVAM_API_KEY)\s*=\s*[A-Za-z0-9_-]{8,})")
leaks = []
for rel in tracked:
    p = ROOT / rel
    if p.suffix in {".png", ".jpg", ".pdf", ".pptx", ".db"} or not p.is_file():
        continue
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if SECRET.search(line):
            leaks.append(f"{rel}:{n}")
check("no API keys or secrets in tracked files", not leaks, ", ".join(leaks))


# Summary --------------------------------------------------------------------------------------

for w in warnings:
    print(f"::warning title=Guardrails::{w}")
summary = {"failures": failures, "warnings": warnings}
print(json.dumps(summary, indent=2, ensure_ascii=False))
sys.exit(1 if failures else 0)
