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
from app.data.seed import build_profiles  # noqa: E402
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

approve_calls = []
for f in py_files("app/agent", "app/rules", "app/planner", "app/forms"):
    for node in ast.walk(ast.parse(f.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("approve",):
            approve_calls.append(f"{f.relative_to(ROOT)}:{node.lineno}")
check("rule 2: agent code never calls queue.approve", not approve_calls, ", ".join(approve_calls))


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

hits = []
for prof in build_profiles():
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

masks = [p["customer_id"] for p in build_profiles()
         if p.get("aadhaar_masked") and not re.fullmatch(r"XXXX-XXXX-\d{4}", p["aadhaar_masked"])]
check("rule 5: every seeded Aadhaar uses the XXXX-XXXX-1234 mask", not masks, str(masks))


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
