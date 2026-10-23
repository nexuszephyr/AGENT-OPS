# CLAUDE.md

All project instructions live in AGENTS.md so every coding assistant reads the same rules. Read it first; the notes below only add what is specific to Claude Code.

@AGENTS.md

## Claude Code notes

- **Verify before you push.** Run the CI checks from AGENTS.md → Commands (`ruff`, `mypy`, `pytest -p randomly --cov`, `check_guardrails.py`, `check_docs_sync.py`, `smoke.py`). None of them need an LLM key: without `LLM_API_KEY` the app runs in rules-only mode.
- **A contract change and its doc change go in the same PR.** That covers tool I/O, API routes, `products.json`, status constants, the system prompt and the personas. `scripts/check_docs_sync.py` fails CI when they drift: fix the doc, never the check.
- **Never weaken a guardrail to make a test or check pass.** If `check_guardrails.py` or a test in `tests/test_review_fixes.py` fails, the code is wrong.
- **PRs always target `main`.** `main` is protected (one approval + **CI passed**). Don't open a PR into another feature branch: work merged that way never reaches `main`, which is what happened to the CI work in PR #3.
- **Secrets and data:** never commit `.env` or a real key; use synthetic personas only, and only masked IDs (`XXXX-XXXX-1234`), including in tests and fixtures.
