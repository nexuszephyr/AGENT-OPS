# Agent tools and API contracts

The LLM sees the tools in section 1 (schemas live in `app/agent/prompts.py`). Each tool is a thin wrapper in `app/agent/tools.py` over pure modules. **Guardrails are enforced inside the tools, not in the prompt.** The model cannot bypass them.

Every tool call and its result is written to `audit_log` (`actor="agent"`).

## 1. LLM-callable tools

### `fetch_customer`
```
in:  { "customer_id": "C001" }
out: { "profile": <Profile> }
```
Reads `customers` and merges `sessions.profile_overrides_json` on top.

### `update_profile`
```
in:  { "facts": { "<field>": <value>, ... }, "evidence": "customer said: 'I grow ragi this kharif'" }
out: { "profile": <Profile>, "rejected_fields": [ {"field": "...", "reason": "unknown field | bad type | conflicts with bank record"} ] }
```
- Only fields defined in `docs/DATA_MODEL.md` §1 are accepted; types are validated.
- **Conflict rule:** compared against the *effective* profile (bank record + facts recorded so far), never just the bank record. A differing value is not written; it comes back in `rejected_fields` with one of:
  - `conflicts with bank record`: the DB has a non-null value (e.g. DB says `has_savings_account=true`, customer says no) → the agent must tell the officer
  - `set by a completed service request; cannot be changed by conversation`: e.g. `aadhaar_seeded` after the seeding request was approved (system facts are listed under `_system_fields` in the overrides)
  - `conflicts with recorded fact`: an earlier interview fact. Only the **officer** (`/facts` route, actor `officer`) may correct one of these
- **Officer-only fields:** `grows_notified_crop` and `in_notified_area` (the PMFBY notified-list check) are refused from the agent with `officer must confirm via the fact form`. Only the officer's `/facts` route can set them.
- `facts` must be an object; anything else is a tool error.
- An agent call with empty `facts` is a tool error (`no facts given…`): it would only burn the tool budget. The officer's `/facts` with empty facts still means "customer doesn't know".
- `evidence` is required and stored in the audit log.

### `check_eligibility`
```
in:  {}
out: { "results": [ <ProductResult>, ... ] }      // format in RULES_SPEC.md §4
```
Runs `rules.engine.evaluate_all(profile)`. Pure and deterministic.

### `pick_next_question`
```
in:  {}
out: { "question": { "question_id": "Q_NOTIFIED_CROP", "fields": ["grows_notified_crop","in_notified_area"],
                     "text_en": "...", "text_kn": "...", "resolves": ["PMFBY"] } }
     or { "question": null, "reason": "no resolvable unknowns" | "question limit reached" }
```
Algorithm (`app/planner/questions.py`):
1. Collect every product with status `UNKNOWN` and its `unknown_fields`.
2. For each question in the catalogue, `score` = number of UNKNOWN products whose missing fields are all covered by that question's `fields`.
3. Pick the highest score; break ties by catalogue order. Skip questions already asked.
4. If `questions_asked >= 5`, return null with reason `"question limit reached"`; the remaining UNKNOWNs become `VERIFY_MANUALLY`.

Question catalogue (extend as needed):

| question_id | fields | text_en |
|---|---|---|
| `Q_ITP` | is_income_tax_payer | Did you pay income tax last year? |
| `Q_NOTIFIED_CROP` | grows_notified_crop, in_notified_area | Which crop are you growing this season, and in which village? *(officer confirms against the notified list)* |
| `Q_LAND` | is_landholder | Do you own farmland in your name? |
| `Q_GIRLS` | girl_children_under_10 | Do you have a daughter younger than 10? |
| `Q_SSY_EXISTING` | existing_ssy_accounts | Does your family already have a Sukanya Samriddhi account? How many? |
| `Q_PENSION` | monthly_pension | Do you receive a pension? About how much per month? |
| `Q_GOVT` | is_govt_employee | Do you or did you work for the government? |
| `Q_PROF` | is_registered_professional | Are you a registered doctor, engineer, lawyer, CA or architect? |
| `Q_SEEDED` | aadhaar_seeded | Is your Aadhaar linked to this bank account? *(officer can check the system)* |
| `Q_EKYC`, `Q_HAS_ACCOUNT`, `Q_AGE`, `Q_OCCUPATION` | one field each | fallbacks for gaps in the bank record (see `app/planner/questions.py`) |

If no single question fully resolves a product, the ranker falls back to the question covering the most missing fields.

### `plan_actions`
```
in:  {}
out: {
  "steps": [
    {"order": 1, "type": "SERVICE_REQUEST", "action_id": "AADHAAR_SEEDING_REQUEST",
     "unlocks": ["PMKISAN_DBT"], "why": "Unlocks DBT for PM-KISAN"},
    {"order": 2, "type": "SERVICE_REQUEST", "action_id": "EKYC_REQUEST", "unlocks": ["PMKISAN_DBT"]},
    {"order": 3, "type": "ENROLLMENT", "product_id": "PMJJBY", "docs_missing": ["NOMINEE","AUTODEBIT_CONSENT"],
     "missing_mandatory": ["nominee_name", "nominee_relation"]},
    ...
  ],
  "checklist": [ {"doc_id": "RTC", "label": "Land record (RTC / Pahani)", "used_by": ["PMKISAN_DBT","PMFBY"]} ]
}
```
Algorithm (`app/planner/documents.py`):
1. Take products with status `ELIGIBLE` or `ELIGIBLE_BLOCKED`.
2. Collect pending prerequisite actions from `blocked_by`; score each by the number of products it unlocks. Order by score desc, then `OPEN_SAVINGS_ACCOUNT` before seeding before eKYC.
3. Then `ELIGIBLE` enrollments ordered by fewest missing docs; then `ELIGIBLE_BLOCKED` enrollments, each placed after its actions.
4. **Docs on file:** if `has_savings_account`, the bank already holds `AADHAAR`, `PHOTO`, `PASSBOOK`, `MOBILE`. Everything else is "customer to bring".
5. `checklist` = deduplicated union of missing docs, each with `used_by`.
6. Every `ENROLLMENT` step has `missing_mandatory`: the extra form fields `fill_form` will still need as `extra_fields` (from `MANDATORY_EXTRA` in `app/forms/fill.py`, passed in by the tool layer so the planner stays pure). The agent calls `fill_form` only once the conversation gives all of them; otherwise it asks the officer for all of them at once.

### `fill_form`
```
in:  { "product_id": "PMJJBY", "extra_fields": { "nominee_name": "...", "nominee_relation": "..." } }
out: { "item_id": "Q-0007", "status": "PENDING_APPROVAL", "pdf_path": "...", "missing_mandatory": [] }
```
- **Refuses** (error) unless the product's current status is `ELIGIBLE`. The rules engine is re-run inside the tool; the LLM's claim isn't trusted.
- Fills fields from the profile. If mandatory extra fields are missing (nominee, girl child's name, crop…; see `MANDATORY_EXTRA` in `app/forms/fill.py`), **no item is created**: it returns `{"item_id": null, "status": "NEEDS_INFO", "missing_mandatory": [...]}`. The agent asks, then calls `fill_form` again.
- Calling `fill_form` again while the item is still `PENDING_APPROVAL` replaces that draft (`queue.replace_pending`). It's audited as `queue_replace` with `{item_id, before, after}` for the changed fields only, and shown on the session timeline, so the checker can see that a draft changed after it was first prepared. `prepared_by` and the revision count stay unchanged.
- Refuses extra fields that look like a full 12-digit Aadhaar number.
- Creates a `queue_items` row (`kind=ENROLLMENT_FORM`) at `PENDING_APPROVAL`.

### `raise_request`
```
in:  { "action_id": "AADHAAR_SEEDING_REQUEST", "note": "..." }
out: { "item_id": "Q-0005", "status": "PENDING_APPROVAL" }
```
- Refuses unless `action_id` appears in `blocked_by` of at least one product for this customer.
- `OPEN_SAVINGS_ACCOUNT` is not a service request: opening the account *is* the PMJDY enrollment, so the tool tells the agent to call `fill_form("PMJDY")`. The plan shows it as one `ENROLLMENT` step for PMJDY that carries `action_id` and `unlocks`.
- Raising the same request twice returns the existing item, including one already `SUBMITTED`.
- Creates a `queue_items` row with `kind=SERVICE_REQUEST`.

### Completion effects (system, not a tool)

When the officer approves an item, `sessions.apply_completion` records what it changes as a system fact (audited as `completion_effect`), so blocked products can unlock:

| Approved item | Fact applied |
|---|---|
| `AADHAAR_SEEDING_REQUEST` | `aadhaar_seeded = true` |
| `EKYC_REQUEST` | `ekyc_done = true` |
| PMJDY enrollment | `has_savings_account = true` |
| any enrollment | product added to `enrollments` (→ `ALREADY_ENROLLED`) |

### `revise_item`
```
in:  { "item_id": "Q-0007", "changes": { "<payload field>": <value> }, "explanation": "Fixed nominee spelling per officer" }
out: { "item_id": "Q-0007", "status": "PENDING_APPROVAL", "revision_count": 1 }
```
- Only for items in `REJECTED`. Only keys already in the payload can change.
- After 2 revisions → status `NEEDS_OFFICER` and the tool returns an error telling the agent to stop.
- **Order is validate → write → render** (`tools.revise_and_render`, shared with the officer route): status, keys and the identifier check all run before anything is written, and the PDF is re-rendered only after the DB write. A failed revision never changes the PDF the officer sees.
- `changes` goes through the same full-Aadhaar check as `fill_form`.

### Cross-cutting behaviour

- **Identifier check** (`app/privacy.py`, one helper for every path): any string that looks like a full Aadhaar number (12 digits, plain or grouped 4-4-4 with space, `-` or `.`), anywhere in a nested payload, is refused by `fill_form`, `queue.create`, `replace_pending` and `revise`. `audit.log` also **redacts** such values (keeping the last 4 digits), because tool args are logged before they are validated.
- **Tool errors stay tool errors:** any exception inside a tool (bad args, a bug) is returned to the model as `{"error": ...}` and logged as an `error` audit event. Only real provider failures switch the turn to rules-only mode.
- **The ranker owns the question budget:** a question in the model's reply counts only if its `question_id` is the ranker's current pick (or a re-ask of the still-pending question). Otherwise it is replaced by the ranker's question, or dropped, and logged as `question_replaced`.

## 2. Tools the LLM must NOT have

`approve_item`, `submit_item`, any SQL access, and any tool that sets an eligibility status. These are officer-only API routes.

## 3. HTTP API (`app/api/main.py`)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | | `{ok, llm_configured, provider}` |
| GET | `/officers` | | list of `{officer_id, name, role}` (`role`: `maker` \| `checker`), for the UI's "Acting as" selector |
| GET | `/customers` | `?segment=demo\|branch` (optional; default all) | list of `{customer_id, name, age, occupation, district, preferred_language, segment}`. `segment` is `demo` (C001–C006) or `branch` (C101–C130); the officer console picker requests `segment=demo` |
| POST | `/scan` | `{officer_id?}` (optional; must be a known officer, else 400) | `ScanResult`: branch-wide proactive scan over every customer (`app/scan.py`). **Recommends only:** it creates no queue items and changes no customer or session. Writes one `scan_run` audit event (actor `agent`, no session, payload = `officer_id` + `summary` only, no per-customer data). The latest result is kept in memory |
| GET | `/scan/latest` | | the last `ScanResult`, or 404 `{"detail": "no scan has run yet"}` |
| POST | `/sessions` | `{customer_id, officer_id}` | `SessionState` (runs ingest + first evaluation). `officer_id` must be a known officer (400 otherwise); it becomes `prepared_by` on every item the session queues |
| POST | `/sessions/{id}/message` | `{text, lang?}` | `{reply: AgentReply, state: SessionState}` |
| GET | `/sessions/{id}/state` | | `SessionState` |
| GET | `/sessions/{id}/timeline` | | `Timeline`: the session's audit log as UI events grouped by pipeline stage (read-only; safe to poll during an agent turn) |
| POST | `/sessions/{id}/facts` | `{facts, question_id?}` | `{result, state}`: officer enters facts directly (same validation as `update_profile`; works with no LLM). With `question_id` it resolves that pending question; empty `facts` = "customer doesn't know" → its products go to `VERIFY_MANUALLY` |
| POST | `/sessions/{id}/act` | `{extra_fields: {PRODUCT_ID: {...}}}` | `{outcomes, state}`: deterministically queues every actionable plan step (rules-only fallback) |
| GET | `/queue` | `?status=PENDING_APPROVAL` | list of queue items |
| POST | `/queue/{item_id}/approve` | `{officer_id}` (**required**) | item (→ `APPROVED`, then mock `SUBMITTED`). **Maker-checker:** `officer_id` must be a `checker` and not the item's `prepared_by`, otherwise **403** and an `approval_denied` audit event; the item is unchanged |
| POST | `/queue/{item_id}/reject` | `{officer_id, reason}` (both **required**) | item (→ `REJECTED`); triggers agent revision turn (rules-only: no-op, the officer revises). Same maker-checker rule and 403 as approve |
| POST | `/queue/{item_id}/revise` | `{officer_id, changes, explanation?}` | officer edits a `REJECTED` or `NEEDS_OFFICER` item → back to `PENDING_APPROVAL` (still needs an explicit approve). The revising officer becomes the item's `prepared_by` (whoever last changed the content is its maker), so a different checker must approve it. Unknown `officer_id` → 400. Agent revisions keep the session officer |
| GET | `/queue/{item_id}/history` | | `ItemHistory`: the item's audit events and what changed since its latest rejection (read-only) |
| GET | `/queue/{item_id}/pdf` | | PDF file |
| GET | `/dashboard` | | `DashboardStats` |
| GET | `/audit` | `?session_id=` | audit rows |

Errors: `MakerCheckerError` → **403**; every other queue/session error → 400; a missing required field → 422.

`SessionState`:
```json
{
  "session_id": "S-001", "customer_id": "C001", "state": "ASKING",
  "profile": { ... },
  "results": [ <ProductResult> ],
  "plan": { "steps": [...], "checklist": [...] },
  "next_question": { "question": {...} | null, "reason"?: "..." },   // the pending question while one awaits its answer
  "pending_question": "Q_ITP" | null,
  "queue_items": [ ... ],
  "questions_asked": 1,
  "messages": [ {"role": "user"|"assistant", "content": "..."} ]
}
```

`Timeline` (built by `app/timeline.py` from `audit_log`; identifiers are already redacted there):
```json
{
  "session_id": "S-001", "state": "ASKING",
  "stage": "decide",                       // ingest | decide | act | report; follows the agent while a turn runs
  "running": false,                        // an officer message has no reply yet, or a tool call has no result yet
  "stages": [ {"id": "ingest", "label": "Ingest", "name": "Data ingestion",
               "status": "done" | "current" | "upcoming", "events": 3, "tools": {"update_profile": 1}} ],
  "events": [ {"id": 42, "ts": "...", "stage": "decide", "actor": "agent" | "officer" | "system",
               "kind": "tool" | "<audit event>", "tool": "check_eligibility" | null,
               "tone": "ok" | "running" | "guardrail" | "attention",
               "title": "Rules engine checked 7 products", "summary": "3 eligible, 2 not eligible, ...",
               "detail": { "args": {...}, "result": {...} } } ]
}
```
A tool call and its result are one event (`tone: "running"` until the result lands). `guardrail` marks a refusal: a tool error, a fact kept from the bank record, the ranker overriding the model's question, or maker-checker blocking an approval (`approval_denied`). An `error` event ends a turn: the LLM fallback ("LLM unavailable") or a bug in the turn ("Agent turn failed", payload `turn_failed`), so `running` never sticks.

`ScanResult` (only customers with ≥ 1 opportunity; ordered as described below):
```json
{
  "generated_at": "2026-10-24T09:00:00+00:00", "customers_scanned": 36,
  "call_list": [{
    "customer_id": "C001", "name": "Ramesh Gowda", "district": "Mandya", "occupation": "farmer", "segment": "demo",
    "opportunities": [{"product_id": "PMKISAN_DBT", "status": "ELIGIBLE_BLOCKED", "value_inr": 6000,
                       "blocked_by": ["AADHAAR_SEEDING_REQUEST", "EKYC_REQUEST"]}, ...],
    "blockers": [{"action_id": "AADHAAR_SEEDING_REQUEST", "unlocks": ["PMKISAN_DBT"]}, ...],
    "urgent": true, "rank": 2, "score": 422000, "value_inr_total": 418000,
    "reason": "Eligible for PMJJBY, PMSBY, APY, PMKISAN_DBT · Aadhaar not seeded + eKYC pending → PM-KISAN payments will fail"
  }],
  "summary": {"customers_with_opportunities": 34, "opportunities_by_product": {"PMJJBY": 28, ...},
              "blockers_by_action": {"AADHAAR_SEEDING_REQUEST": 3, ...}, "urgent_count": 4, "value_inr_unclaimed": 10846000}
}
```
- **opportunities:** products `ELIGIBLE` or `ELIGIBLE_BLOCKED` (`ALREADY_ENROLLED`, `NOT_ELIGIBLE`, `UNKNOWN` excluded). `value_inr` comes from the rules result and is `null` until #36 adds it.
- **blockers:** the plan's prerequisite actions, with the products each unlocks.
- **urgent:** `AADHAAR_SEEDING_REQUEST` or `EKYC_REQUEST` blocks `PMKISAN_DBT`. The reason names whichever are missing ("Aadhaar not seeded", "eKYC pending", or both joined by " + ") → "PM-KISAN payments will fail".
- **Ordering (`rank`, 1-based) is lexicographic:** urgent first, then `value_inr_total` desc, then number of opportunities desc, then `customer_id` asc. Urgency always wins, so ₹ values (#36) can't push a failing PM-KISAN credit down the list.
- **score** is for display only (not used for ordering): Σ `value_inr` (0 if missing) + 1000 × number of opportunities.

`ItemHistory` (built by `queue.history` from `audit_log`):
```json
{
  "item_id": "Q-1A2B3C", "status": "PENDING_APPROVAL", "revision_count": 1,
  "rejection": {"reason": "Nominee name is Anjali, not Anjeli", "officer_id": "manager-1", "ts": "..."} | null,
  "diff": [ {"field": "nominee_name", "before": "Anjeli", "after": "Anjali"} ],   // since the latest reject
  "events": [ {"id": 12, "ts": "...", "event": "queue_create" | "queue_replace" | "reject" | "revise" | "escalate" | "approve"
               | "approval_denied" | "submit_mock", "actor": "...", ...the audit payload} ]
}
```
`revise` audit events record `before` (the replaced values) next to `changes`, so the diff shows old → new. A `queue_replace` (a pending draft refilled by `fill_form`) after the rejection counts too, so the checker sees every change since the rejection. A field changed back to its old value is left out.

`DashboardStats`:
```json
{
  "customers_screened": 6,
  "products_eligible": 19,
  "items_pending": 3, "items_approved": 14, "items_rejected": 1,
  "value_inr_unlocked": 412000, "value_inr_eligible": 1036000,
  "blocked_by_action": {"AADHAAR_SEEDING_REQUEST": 1, "OPEN_SAVINGS_ACCOUNT": 1},
  "not_eligible_reasons": {"R-APY-AGE-MAX": 3, "R-APY-NOT-ITP": 1},
  "top_missing_docs": {"NOMINEE": 5, "RTC": 2}
}
```

₹ values (#36) are **indicative cover / annual benefit** from `products.json` (`value_inr`), never money paid out. Products with `value_inr: null` (no single number: PMJDY, SSY, PMFBY) are skipped, never counted as ₹0.
- **`value_inr_unlocked`** = Σ `value_inr` over `ENROLLMENT_FORM` items that are `APPROVED` or `SUBMITTED` (so a checker approved them). Service requests carry no value, and each product counts once per customer.
- **`value_inr_eligible`** = Σ `value_inr` over `ELIGIBLE` + `ELIGIBLE_BLOCKED` products in each screened customer's latest session, excluding `ALREADY_ENROLLED` and anything already counted as unlocked.
