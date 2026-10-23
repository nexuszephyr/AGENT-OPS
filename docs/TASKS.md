# Build plan

Owners: **Om** = rules, agent, planner, API · **Abhinav** = data, forms, UI, dashboard.
Work on branches `om/*` and `abhinav/*`; merge to `main` only with tests passing.

## Phase 0: tonight (setup + core logic)

| ID | Task | Owner | Done when |
|---|---|---|---|
| T0 | Repo skeleton per AGENTS.md layout, `requirements.txt`, `.env.example`, `.gitignore` (bank.db, .env, out/) | Om | `pytest` runs (0 tests) |
| T1 | `products.json` from RULES_SPEC §6; verify ⚠ values from REFERENCE.md, fill `last_verified` | Om | file loads, validation passes |
| T2 | `rules/engine.py`: Kleene evaluator, all-failures reporting, prerequisites, startup validation | Om | all tests in RULES_SPEC §7 pass |
| T3 | `data/seed.py`: 6 personas from DATA_MODEL §3, all tables created | Abhinav | `python -m app.data.seed` creates bank.db |
| T4 | `planner/questions.py` + `planner/documents.py` | Om | Ramesh plan starts with seeding; Arjun's next question is `Q_ITP` |
| T5 | `forms/fill.py`: one reportlab template per product + one per service request, masked fields, "DRAFT – pending officer approval" watermark | Abhinav | PDFs generate for Ramesh and Priya |
| T6 | `queue.py` + `audit.py` with enforced transitions | Abhinav | `tests/test_queue.py` passes (illegal transitions raise) |

## Phase 1: agent + API

| ID | Task | Owner | Done when |
|---|---|---|---|
| T7 | `agent/llm.py`: provider-agnostic chat with tool calling, retries, timeout | Om | a dummy tool call round-trips |
| T8 | `agent/tools.py` per TOOLS.md, with guardrails inside tools | Om | unit tests: `fill_form` refuses NOT_ELIGIBLE; `raise_request` refuses irrelevant actions |
| T9 | `agent/loop.py`: state machine, limits, AgentReply parsing, rules-only fallback | Om | Ramesh session runs end to end from the CLI |
| T10 | `api/main.py`: all routes in TOOLS.md §3 | Om | curl walkthrough works |

## Phase 2: UI + reporting

| ID | Task | Owner | Done when |
|---|---|---|---|
| T11 | Streamlit **Officer console**: customer picker, chat, live status panel (colour per status, click → rule + source), plan + checklist | Abhinav | Ramesh demo works in UI |
| T12 | **Approval queue** tab: list, PDF preview, approve / reject with reason; revision shows up after reject | Abhinav | Priya rejection → revision loop works |
| T13 | **Dashboard** tab: KPI tiles, blocked-by-action, top rejection rules, top missing docs, audit table | Abhinav | numbers match DB |
| T14 | Kannada: customer-facing text in `kn`; optional Sarvam STT | Om | Ramesh's question appears in Kannada |

## Phase 3: hardening (event day, by 16:30)

| ID | Task | Owner |
|---|---|---|
| T15 | Run DEMO.md acceptance tests A1–A8; fix | both |
| T16 | Reset script: `python -m app.data.seed --reset` before each demo run | Abhinav |
| T17 | Record a backup demo video | Abhinav |
| T18 | Update slides to BFSI framing; rehearse 4-min script twice | Om |

## Cut order if behind

1. Voice (Sarvam STT)
2. Kannada output (keep English)
3. Dashboard charts → plain numbers
4. Question ranking → fixed catalogue order

**Never cut:** rules engine, approval gate, planner, audit log.

## `requirements.txt` (starting point)

```
fastapi
uvicorn[standard]
pydantic>=2
streamlit
requests
reportlab
python-dotenv
pytest
# pick one LLM SDK:
anthropic
# openai
# google-genai
```
