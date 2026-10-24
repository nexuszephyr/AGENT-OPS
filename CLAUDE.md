# CLAUDE.md

All project instructions live in AGENTS.md so every coding assistant reads the same rules. Read it first; the notes below only add what is specific to Claude Code.

@AGENTS.md

## Claude Code notes

- **Verify before you push.** Run the CI checks from AGENTS.md → Commands (`ruff`, `mypy`, `pytest -p randomly --cov`, `check_guardrails.py`, `check_docs_sync.py`, `smoke.py`). None of them need an LLM key: without `LLM_API_KEY` the app runs in rules-only mode.
- **A contract change and its doc change go in the same PR.** That covers tool I/O, API routes, `products.json`, status constants, the system prompt and the personas. `scripts/check_docs_sync.py` fails CI when they drift: fix the doc, never the check.
- **Never weaken a guardrail to make a test or check pass.** If `check_guardrails.py` or a test in `tests/test_review_fixes.py` fails, the code is wrong. Only `app/queue.py` and the officer routes in `app/api/main.py` may touch `queue.approve`, `queue.reject`, `queue._move` or write `queue_items`; the check catches aliased imports, `getattr` and raw SQL too. Rule 2 scans `app/` and `ui/` only. Test harnesses in `tests/` and `scripts/` (e.g. `scripts/live_llm_check.py`) may call `queue.approve`/`reject` while playing the **checker** (`manager-1`; items are prepared by the maker `officer-1`), exactly the way the routes do. App and UI code may not. The officer-id edits to `tests/test_review_fixes.py` in #39 (approve/reject as `manager-1`) were a deliberate contract change for maker-checker, not a weakened test: don't revert them.
- **The branch scan only recommends.** `app/scan.py` stays pure (rules engine + planner, no DB, LLM or queue), and `POST /scan` writes nothing but its `scan_run` audit row. Never let it queue items or change customers or sessions: the officer opens a session from the call list, and the approval gate (including maker-checker) applies there.
- **Item history is read-only.** `queue.history` / `GET /queue/{id}/history` only reads `audit_log` (it powers the revision diff). Never register it as a `ToolBox` tool or let it write: decisions still go only through the officer approve / reject routes.
- **PRs always target `main`.** `main` is protected (one approval + **CI passed**). Don't open a PR into another feature branch: work merged that way never reaches `main`, which is what happened to the CI work in PR #3.
- **Secrets and data:** never commit `.env` or a real key; use synthetic personas only, and only masked IDs (`XXXX-XXXX-1234`), including in tests and fixtures.
