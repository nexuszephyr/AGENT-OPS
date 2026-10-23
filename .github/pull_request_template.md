## What and why

<!-- One or two sentences. Link the task from docs/TASKS.md if there is one. -->

## How it was tested

- [ ] `pytest` passes locally
- [ ] Walked at least one demo persona end to end (which one?)

## Guardrails (AGENTS.md): CI checks most of these, but confirm

- [ ] Eligibility is still decided only by `app/rules/engine.py`
- [ ] Nothing reaches `SUBMITTED` without `POST /queue/{id}/approve`
- [ ] New rules cite a `source_url` and `last_verified`
- [ ] Missing data → `UNKNOWN` / `VERIFY_MANUALLY`, never a default
- [ ] No real or unmasked IDs in code, data, docs or logs
- [ ] New agent behaviour writes to `audit_log`
- [ ] Docs updated if a contract changed (`scripts/check_docs_sync.py` passes)
