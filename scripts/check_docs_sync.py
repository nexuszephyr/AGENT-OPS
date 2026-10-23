"""Docs and code must agree (AGENTS.md: "update the relevant doc if a contract changed").

    python scripts/check_docs_sync.py

Checks:
  1. app/rules/products.json is identical to the JSON block in docs/RULES_SPEC.md §6
  2. every LLM tool in TOOL_SCHEMAS has a section in docs/TOOLS.md
  3. every FastAPI route is listed in the docs/TOOLS.md HTTP table, and vice versa
  4. every status constant is documented in docs/RULES_SPEC.md
  5. every demo persona in docs/DATA_MODEL.md exists in the seed data
  6. the system prompt in docs/PROMPTS.md matches app/agent/prompts.py (rules section)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.prompts import SYSTEM_PROMPT, TOOL_SCHEMAS  # noqa: E402
from app.api.main import app  # noqa: E402
from app.data.seed import PERSONAS  # noqa: E402
from app.rules import engine  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# 1 · products.json == RULES_SPEC §6
spec = doc("docs/RULES_SPEC.md")
m = re.search(r"## 6\..*?```json\n(.*?)```", spec, re.S)
spec_json = json.loads(m.group(1)) if m else None
code_json = json.loads((ROOT / "app/rules/products.json").read_text(encoding="utf-8"))
diff = ""
if spec_json is not None and spec_json != code_json:
    for a, b in zip(spec_json.get("products", []), code_json.get("products", []), strict=False):
        if a != b:
            diff = f"first difference in product {b.get('id')}"
            break
    diff = diff or "documents/actions/version differ"
check("products.json matches the JSON in RULES_SPEC.md §6", spec_json == code_json,
      diff or "no JSON block found under '## 6.'")

# 2 · tools documented
tools_md = doc("docs/TOOLS.md")
missing_tools = [t["name"] for t in TOOL_SCHEMAS if f"### `{t['name']}`" not in tools_md]
check("every LLM tool has a section in TOOLS.md", not missing_tools, str(missing_tools))

# 3 · routes documented (both directions)
code_routes = set()
for r in app.routes:
    methods = getattr(r, "methods", None) or set()
    path = getattr(r, "path", "")
    if path.startswith(("/docs", "/openapi", "/redoc")):
        continue
    for meth in methods - {"HEAD", "OPTIONS"}:
        code_routes.add((meth, re.sub(r"\{[^}]+\}", "{}", path)))
doc_routes = {(m_, re.sub(r"\{[^}]+\}", "{}", p))
              for m_, p in re.findall(r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`", tools_md, re.M)}
check("every API route is documented in TOOLS.md §3", code_routes <= doc_routes,
      f"undocumented: {sorted(code_routes - doc_routes)}")
check("every documented route exists in the API", doc_routes <= code_routes,
      f"documented but missing: {sorted(doc_routes - code_routes)}")

# 4 · statuses documented
missing_status = [s for s in engine.ALL_STATUSES if f"`{s}`" not in spec]
check("every status constant is documented in RULES_SPEC.md §1", not missing_status, str(missing_status))

# 5 · personas
data_md = doc("docs/DATA_MODEL.md")
doc_ids = set(re.findall(r"^\|\s*`(C\d{3})`", data_md, re.M))
seed_ids = {p["customer_id"] for p in PERSONAS}
check("DATA_MODEL.md personas match the seed data", doc_ids == seed_ids,
      f"docs only: {sorted(doc_ids - seed_ids)}; seed only: {sorted(seed_ids - doc_ids)}")

# 6 · system prompt rules are the reviewed copy
prompts_md = doc("docs/PROMPTS.md")
m = re.search(r"## 1\. Orchestrator system prompt\s+```text\n(.*?)```", prompts_md, re.S)
doc_rules = set()
if m:
    doc_rules = {ln.strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ")}
code_rules = {ln.strip() for ln in SYSTEM_PROMPT.splitlines() if ln.strip().startswith("- ")}
check("system-prompt bullet lines are identical in PROMPTS.md and prompts.py", doc_rules == code_rules,
      f"docs only: {sorted(doc_rules - code_rules)}; code only: {sorted(code_rules - doc_rules)}")

sys.exit(1 if failures else 0)
