# Yojana Saathi

**An onboarding agent that helps bank branches enrol customers in government-linked financial products.**

Team **Agentic Monkeys** (HX-FPA2): Om Bismay Rout (lead), Abhinav Dhiman
AgentOps '26 · MIT Bengaluru × CGI · Domain: **BFSI** · Track: Autonomous workflow automation

## The problem

Banks are the delivery channel for India's social-security and direct-benefit-transfer (DBT) schemes: Jan Dhan accounts, PMJJBY and PMSBY insurance, Atal Pension Yojana, Sukanya Samriddhi, crop insurance, and the Aadhaar-seeded accounts that PM-KISAN pays into. At a branch or a banking-correspondent point, staff screen customers one by one, by hand, across products that each have different age limits, exclusions and paperwork. Eligible customers leave unenrolled, applications bounce for missing documents, and DBT payments fail because accounts aren't seeded.

## What the agent does

| CGI stage | Yojana Saathi |
|---|---|
| **Data ingestion** | Pulls the customer record from a (mock) core-banking DB, then interviews the customer in Kannada or English to fill gaps |
| **Intelligent decision-making** | Deterministic rules engine → eligible / not eligible / unknown per product, with cited rules. Agent picks the most informative next question and plans prerequisites and documents in dependency order |
| **Action execution** | Pre-fills enrollment PDFs, raises service requests (Aadhaar seeding, eKYC, account opening), builds the customer's document checklist; all queued for officer approval |
| **Outcome reporting** | Branch dashboard: customers screened, enrollments pending, approved, blocked (with reasons), missing documents; plus a full audit log |

**Human oversight:** a branch officer approves or rejects every action. If something is rejected, the agent revises it and puts it back in the queue.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add your LLM key (optional, see below)
python -m app.data.seed --reset # run before every demo
uvicorn app.api.main:app --reload        # terminal 1, :8000
streamlit run ui/streamlit_app.py        # terminal 2, :8501
pytest -q                                # 97 tests, no API key needed
```

**No API key? It still works.** Without `LLM_API_KEY` the app runs in *rules-only mode*: statuses, plan and next question come from the deterministic modules; the officer records answers with the fact form and queues steps with "Prepare all". This is also the Wi-Fi-down backup for the demo.

### CI

Every PR runs `.github/workflows/ci.yml`. **CI passed** is the single required check:

| Job | What it checks |
|---|---|
| Lint & types | ruff (style, bugs, bandit security rules), mypy (strict on `rules/`, `planner/`, `privacy`) |
| Tests | pytest on Python 3.11 / 3.12 / 3.13, random order, coverage ≥ 80% |
| Guardrails & docs sync | AGENTS.md rules checked statically (no approve tool for the LLM, pure rules engine, sourced rules, no full Aadhaar, no secrets) + docs ↔ code contracts |
| End-to-end smoke | boots the real API and UI, walks DEMO.md A1–A7 over HTTP, renders the UI, fails on any server traceback |
| Dependency audit | pip-audit for known CVEs (also runs weekly) |

### Code map

| Path | What it is |
|---|---|
| `app/rules/engine.py` + `products.json` | Three-valued eligibility engine: the only thing that decides eligibility |
| `app/planner/questions.py` | Next-question ranker |
| `app/planner/documents.py` | Prerequisite + document dependency planner |
| `app/agent/tools.py` | LLM tools with guardrails inside them |
| `app/agent/llm.py` | Tool-calling loop (Anthropic; OpenAI path untested) |
| `app/agent/loop.py` | Orchestrator, rules-only fallback, rejection → revision turn |
| `app/sessions.py` | Session store, state machine, completion effects |
| `app/queue.py` / `app/audit.py` | Approval-queue state machine / audit log |
| `app/forms/fill.py` | Masked, watermarked PDF forms |
| `app/api/main.py` | FastAPI routes |
| `ui/streamlit_app.py` | Officer console, approval queue, dashboard |

## Docs

| File | What it covers |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules for AI coding assistants working in this repo |
| [REFERENCE.md](REFERENCE.md) | The 7 products: eligibility, documents, benefits, sources |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, agent state machine |
| [docs/RULES_SPEC.md](docs/RULES_SPEC.md) | Rules JSON format and three-valued evaluation |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Customer schema, DB tables, demo personas |
| [docs/TOOLS.md](docs/TOOLS.md) | Agent tool contracts and API endpoints |
| [docs/PROMPTS.md](docs/PROMPTS.md) | LLM system prompt and structured outputs |
| [docs/RESPONSIBLE_AI.md](docs/RESPONSIBLE_AI.md) | Guardrails, approval gate, privacy |
| [docs/TASKS.md](docs/TASKS.md) | Build plan and owners |
| [docs/DEMO.md](docs/DEMO.md) | Demo script and acceptance tests |

## Disclaimer

Prototype for a hackathon. Uses synthetic customer data only. Scheme rules are simplified from public sources (see REFERENCE.md) and must be verified against official notifications before any real use.
