# AGENTS.md: instructions for coding agents

This file is the entry point for any AI coding assistant (Claude Code, Cursor, Codex, Copilot) working in this repo. Read it fully before writing code. `CLAUDE.md` imports this file.

## What we are building

**Yojana Saathi**: a BFSI agent for bank branch officers and banking correspondents (BCs). It screens a customer for government-linked financial products, decides eligibility with a deterministic rules engine, plans the documents and prerequisite actions, pre-fills enrollment forms, queues every action for officer approval, and reports outcomes on a branch dashboard.

Hackathon: AgentOps '26 (MIT Bengaluru × CGI), 24 Oct 2026. Domain: **BFSI**. Track: **Autonomous workflow automation**.
CGI's required pipeline, which the demo must show end to end:

```
Data ingestion → Intelligent decision-making → Action execution → Outcome reporting
```

Read next, in order:
1. `README.md`: overview and how to run
2. `docs/ARCHITECTURE.md`: components and data flow
3. `REFERENCE.md`: the 7 products, eligibility rules, documents, sources
4. `docs/RULES_SPEC.md`: rules JSON format and evaluation semantics
5. `docs/DATA_MODEL.md`: customer schema, DB tables, demo personas
6. `docs/TOOLS.md`: agent tool contracts
7. `docs/PROMPTS.md`: LLM system prompt and output formats
8. `docs/RESPONSIBLE_AI.md`: guardrails that must not be broken
9. `docs/TASKS.md`: sprint snapshot (the GitHub issues and milestones are the plan of record)
10. `docs/DEMO.md`: demo script and acceptance tests
11. `docs/UI.md`: UI design system (theme, status palette, shared components)

## Non-negotiable rules

1. **The LLM never decides eligibility.** Only `app/rules/engine.py` returns ELIGIBLE / NOT_ELIGIBLE / UNKNOWN. The LLM may interview, extract facts, explain, choose the next question, and draft text. If you find yourself asking the model "is this customer eligible", stop and call `check_eligibility` instead.
2. **Nothing is submitted without officer approval.** Every action (form, service request) goes into the approval queue as `PENDING_APPROVAL`. Only `POST /queue/{id}/approve` moves it on. No code path may skip the gate. Maker-checker: only a `checker` who did not prepare the item (`prepared_by`) may approve or reject it (`app/queue.py`, roles in `app/officers.py`).
3. **Every eligibility decision cites its rule and source URL.** Rules without a `source_url` fail validation at startup.
4. **Missing or conflicting data → UNKNOWN, never a guess.** The agent asks a question or marks the product "verify manually".
5. **Synthetic data only.** No real names, Aadhaar, PAN or account numbers. Aadhaar and account numbers are always stored and displayed masked (`XXXX-XXXX-1234`).
6. **Everything the agent does is written to `audit_log`.**
7. **Keep it demo-able.** Prefer simple, working code over clever abstractions. The demo matters more than generality.

## Tech stack (do not add frameworks without a reason)

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| API | FastAPI + Uvicorn |
| UI | Streamlit (officer console, approval queue, dashboard) |
| DB | SQLite via `sqlite3` (no ORM needed) |
| LLM | Provider-agnostic wrapper in `app/agent/llm.py`; set via env vars |
| PDFs | reportlab (generate); pypdfium2 (UI preview of page 1) |
| Tests | pytest |

Environment variables (put them in `.env`, never commit them):

```
LLM_PROVIDER=anthropic|openai   # openai path is untested
LLM_MODEL=<model id>
LLM_API_KEY=...
SARVAM_API_KEY=           # unused (voice input was dropped)
DB_PATH=app/data/bank.db
YS_OFFICER_ID=officer-1   # optional (UI): initial "Acting as" officer (officer-1 maker, manager-1 checker)
YS_BRANCH=...             # optional (UI): branch shown in the header
```

## Repo layout

```
app/
  api/main.py              FastAPI app and routes
  agent/
    loop.py                orchestrator: tool-calling loop, state machine
    tools.py               tool implementations (thin wrappers over modules below)
    llm.py                 provider-agnostic chat + tool-calling client
    prompts.py             system prompt, tool schemas
  rules/
    products.json          the 7 products' rules (source of truth)
    engine.py              3-valued rule evaluator
  planner/
    documents.py           prerequisite and document dependency planner
    questions.py           next-question ranker
  forms/
    fill.py                reportlab PDF generation
  data/
    seed.py                creates bank.db with the synthetic customers
    bank.db                (generated, gitignored)
  queue.py                 approval queue state machine
  sessions.py              session store, derived state, completion effects
  privacy.py               full-Aadhaar detection / redaction (every write path + audit log)
  audit.py                 audit log helper
  officers.py              officer registry + roles (maker / checker) for maker-checker approval
  scan.py                  branch-wide proactive scan: ranked call list (pure, recommend-only)
  timeline.py              audit log → Ingest/Decide/Act/Report timeline for the UI (pure, read-only)
  db.py                    SQLite schema + in-place column migrations; one connection, serialized per statement
  config.py                env settings and stop limits (questions, tool calls, revisions)
ui/streamlit_app.py        officer console | approval queue | dashboard tabs
ui/components.py           shared UI components + status palette (docs/UI.md); theme in .streamlit/config.toml
tests/
  test_rules.py            rule engine + the two demo traps
  test_planner.py
  test_queue.py
  test_tools.py            guardrails inside the LLM tools
  test_agent_loop.py       scripted fake-LLM turns
  test_api.py              DEMO.md acceptance tests over HTTP
  test_review_fixes.py     regression tests for the PR #2 review
  test_ui_components.py    UI palette ↔ engine statuses, contrast, HTML escaping, theme ↔ tokens
  test_ui_console.py       officer console: plan stepper follows the real queue, new components escape text
  test_timeline.py         session timeline: stages, folded tool calls, guardrail marks, live "running", redaction
  test_db_concurrency.py   the shared connection under concurrent agent writes + timeline polls
  test_queue_history.py    item history + revision diff since the latest rejection (GET /queue/{id}/history)
  test_scan.py             branch scan: ranking, urgency, totals, recommend-only (no writes), masking
  test_maker_checker.py    maker-checker: the preparer can't approve, roles, prepared_by, 403s
  test_prompt_hardening.py fake-LLM regressions from the live run (#12): plan fields, siblings, retry, officer-only facts
scripts/
  check_guardrails.py      static checks for the non-negotiable rules
  check_docs_sync.py       docs/ ↔ code contract checks
  smoke.py                 boots API + UI and walks DEMO.md A1–A7
  live_llm_check.py        real-LLM scenario run → docs/LIVE_LLM_RUN.md (manual only, needs a key; never in CI)
.github/workflows/ci.yml   CI; the required check is "CI passed"
docs/
```

## Commands

```bash
pip install -r requirements.txt
python -m app.data.seed --reset          # (re)build bank.db with demo customers; wipes sessions, queue, audit
uvicorn app.api.main:app --reload        # API on :8000
streamlit run ui/streamlit_app.py        # UI on :8501
pytest -q                                # must pass before every demo run
```

CI (`.github/workflows/ci.yml`) runs on every PR. Run the same checks locally before pushing:

```bash
pip install -r requirements-dev.txt
ruff check . && mypy app                 # lint + types
pytest -p randomly --cov --cov-fail-under=80
python scripts/check_guardrails.py       # the non-negotiable rules above, checked statically
python scripts/check_docs_sync.py        # docs/ and code agree (products.json, tools, routes, prompt)
python scripts/smoke.py                  # boots API + UI and walks DEMO.md A1–A7
```

## Coding conventions

- Type hints everywhere; use `pydantic` models for API and tool I/O.
- Pure functions in `rules/` and `planner/` (no DB, no LLM): easy to test.
- The tool functions in `agent/tools.py` return JSON-serialisable dicts that match `docs/TOOLS.md` exactly.
- Status strings are constants defined once in `app/rules/engine.py`: `ELIGIBLE`, `ELIGIBLE_BLOCKED`, `NOT_ELIGIBLE`, `UNKNOWN`, `ALREADY_ENROLLED`, `VERIFY_MANUALLY` (see `docs/RULES_SPEC.md`).
- Money in integer rupees. Dates in ISO 8601.
- Log every tool call with `audit.log(session_id, actor, event, payload)`.
- If an LLM call fails, the app must still show the rules-engine result (degrade gracefully).

## Definition of done for any task

- Code works end to end on at least one demo persona.
- `pytest -q` passes, and the CI job **CI passed** is green.
- No guardrail from the list above is weakened.
- Update the relevant doc if a contract changed.
