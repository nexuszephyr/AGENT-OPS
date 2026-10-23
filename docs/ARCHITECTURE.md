# Architecture

## Component diagram

```
┌──────────────────────── Streamlit UI (ui/streamlit_app.py) ────────────────────────┐
│  Tab 1: Officer console        Tab 2: Approval queue        Tab 3: Branch dashboard │
│  (pick customer, chat,          (approve / reject with       (KPIs, blocked reasons,│
│   live product status panel)     reason)                      audit log)            │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │ HTTP (JSON)
┌───────────────────────────── FastAPI (app/api/main.py) ────────────────────────────┐
│ /sessions  /sessions/{id}/message  /sessions/{id}/state  /queue  /dashboard  /audit │
└───────────────────────────────────────┬────────────────────────────────────────────┘
                                        │
                      ┌──── Agent orchestrator (app/agent/loop.py) ────┐
                      │  LLM tool-calling loop + session state machine │
                      └──┬──────┬──────────┬──────────┬──────────┬────┘
                         │      │          │          │          │
               fetch_customer  check_   pick_next_  plan_     fill_form /
               update_profile  eligibility question  actions   raise_request
                  (SQLite)     (rules/    (planner/  (planner/  (forms/ + queue.py)
                               engine)    questions) documents)
                         │                                       │
                         └──────────────► audit.py ◄─────────────┘
                                              │
                                     SQLite: bank.db
              (customers, sessions, queue_items, audit_log)
```

## Mapping to CGI's pipeline

| Stage | Components |
|---|---|
| Data ingestion | `fetch_customer` (core-banking mock), `update_profile` (facts the LLM extracts from the interview), optional Sarvam translation/STT |
| Intelligent decision-making | `rules/engine.py` (eligibility), `planner/questions.py` (next question), `planner/documents.py` (prerequisites + docs order) |
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
```

Limits (stop rules):
- Max **5** questions per session; after that the remaining UNKNOWNs become `VERIFY_MANUALLY`.
- Max **8** tool calls per agent turn, and max **2** revisions per rejected item; after that, escalate to the officer as `NEEDS_OFFICER`.
- If the LLM call fails, the session falls back to "rules-only mode": statuses still shown, and the next question comes from a fixed order.

## Request flow (one officer message)

1. UI → `POST /sessions/{id}/message {text}`
2. `loop.py` appends the message and calls the LLM with the system prompt, session state and tool schemas.
3. The LLM calls tools (e.g. `update_profile` → `check_eligibility` → `pick_next_question`).
4. Each tool result goes back to the LLM; every call is written to `audit_log`.
5. The LLM returns a structured `AgentReply` (see `docs/PROMPTS.md`).
6. The API returns the reply + the fresh `SessionState` (product statuses, plan, queue items).
7. The UI re-renders the status panel.

## Why each piece exists

- **Rules engine, separate from the LLM:** this makes eligibility deterministic, testable and auditable. It's the core responsible-AI claim.
- **Planner as pure code:** dependency ordering is a graph problem, not a language problem.
- **LLM orchestrator:** decides the conversation flow, phrases questions in the customer's language, explains decisions, and revises rejected items using the officer's free-text reason. These are things a fixed script can't do well.
- **Approval queue:** the human-oversight requirement from CGI.
