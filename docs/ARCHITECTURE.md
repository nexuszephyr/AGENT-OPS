# Architecture

## Component diagram

```
┌──────────────────────── Streamlit UI (ui/streamlit_app.py) ────────────────────────┐
│  Officer console     Branch scan          Approval queue        Branch dashboard    │
│  (customer, chat,    (run scan, ranked    (approve / reject,    (KPIs, blocked      │
│   live pipeline)      call list → start)   diff, PDF preview)    reasons, audit log)│
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │ HTTP (JSON)
┌───────────────────────────── FastAPI (app/api/main.py) ────────────────────────────┐
│ /sessions  …/message  …/state  …/facts  …/act  /queue (approve|reject|revise|pdf)   │
│ /dashboard  /audit  /health  /customers  /scan  …/timeline  /queue/{id}/history     │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │
                      ┌──── Agent orchestrator (app/agent/loop.py) ────┐
                      │  LLM tool-calling loop; state in sessions.py   │
                      └──┬──────┬──────────┬──────────┬──────────┬────┘
                         │      │          │          │          │
               fetch_customer  check_   pick_next_  plan_     fill_form /
               update_profile  eligibility question  actions   raise_request
                  (SQLite)     (rules/    (planner/  (planner/  (forms/ + queue.py)
                               engine)    questions) documents)
                         │                                       │
                         └──────► audit.py (privacy.py redacts) ◄┘
                                              │
                                     SQLite: bank.db
              (customers, sessions, queue_items, audit_log)
```

## Mapping to CGI's pipeline

| Stage | Components |
|---|---|
| Data ingestion | `fetch_customer` (core-banking mock), `update_profile` (facts the LLM extracts from the interview) |
| Intelligent decision-making | `rules/engine.py` (eligibility), `planner/questions.py` (next question), `planner/documents.py` (prerequisites + docs order), `scan.py` (branch-wide proactive scan: ranked call list, recommend-only) |
| Action execution | `forms/fill.py` (PDFs), `raise_request` (seeding / eKYC / account opening), `queue.py` (approval gate) |
| Outcome reporting | `/dashboard`, `/audit`, Streamlit dashboard tab |

## Agent session state machine

```
           start
             │
             ▼
        INGESTING ── fetch_customer, extract facts from first message
             │
             ▼
        EVALUATING ── check_eligibility (all products)
             │
     any UNKNOWN that a question can resolve?
        │ yes                              │ no
        ▼                                  ▼
     ASKING ── pick_next_question       PLANNING ── plan_actions
        │   (wait for answer,              │
        │    update_profile)               ▼
        └──────► EVALUATING            ACTING ── fill_form / raise_request
                                           │   (each → queue as PENDING_APPROVAL)
                                           ▼
                                   AWAITING_APPROVAL
                                     │            │
                               approved        rejected(reason)
                                     │            │
                                     ▼            ▼
                                 (mark item    REVISING ── agent fixes item
                                  APPROVED)       │       using reason → requeue
                                     │            └──► AWAITING_APPROVAL
                                     ▼
                       all items resolved → DONE (dashboard updates)

   any item NEEDS_OFFICER (revision limit hit) → ESCALATED until the officer revises it
```

Limits (stop rules):
- Max **5** questions per session. Remaining UNKNOWNs become `VERIFY_MANUALLY` as soon as nothing more can be asked (limit reached, or every useful question was asked and went unanswered) **and** no question is still awaiting its answer (`pending_question`).
- A pending question is resolved only when (a) its fields get recorded, (b) the agent sets `question_unanswered`, or (c) the officer records an answer / clicks "Customer doesn't know" (`POST /facts` with the `question_id` and no facts). An unrelated message leaves it pending, and while it is pending it is the only question that can be asked (a re-ask costs no budget).
- Max **8** tool calls per agent turn, and max **2** revisions per rejected item; after that, escalate to the officer as `NEEDS_OFFICER`.
- If the LLM call fails, the session falls back to "rules-only mode": statuses still shown, and the next question comes from a fixed order.

## Request flow (one officer message)

1. UI → `POST /sessions/{id}/message {text}`, sent from a background thread so the page stays live.
2. `loop.py` appends the message and calls the LLM with the system prompt, session state and tool schemas.
3. The LLM calls tools (e.g. `update_profile` → `check_eligibility` → `pick_next_question`).
4. Each tool result goes back to the LLM; every call is written to `audit_log` as it happens.
5. Meanwhile the UI polls `GET /sessions/{id}/timeline` every second: `app/timeline.py` turns the audit log into
   events grouped by pipeline stage (Ingest → Decide → Act → Report), so each tool call appears live (#18).
6. The LLM returns a structured `AgentReply` (see `docs/PROMPTS.md`).
7. The API returns the reply + the fresh `SessionState` (product statuses, plan, queue items).
8. The UI re-renders the whole console.

**Rules-only path (no LLM key, or the LLM call fails):** the same tools run without the model. The officer records answers with `POST /sessions/{id}/facts` (or "Customer doesn't know": the `question_id` with no facts), queues every actionable step with `POST /sessions/{id}/act` ("Prepare all steps"), and fixes rejected or escalated items with `POST /queue/{id}/revise`. Every item still waits for an explicit approve.

## Why each piece exists

- **Rules engine, separate from the LLM:** this makes eligibility deterministic, testable and auditable. It's the core responsible-AI claim.
- **Planner as pure code:** dependency ordering is a graph problem, not a language problem.
- **LLM orchestrator:** decides the conversation flow, phrases questions in the customer's language, explains decisions, and revises rejected items using the officer's free-text reason. These are things a fixed script can't do well.
- **Approval queue:** the human-oversight requirement from CGI.
