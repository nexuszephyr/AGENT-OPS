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
cp .env.example .env            # add your LLM key
python -m app.data.seed
uvicorn app.api.main:app --reload
streamlit run ui/streamlit_app.py
```

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
