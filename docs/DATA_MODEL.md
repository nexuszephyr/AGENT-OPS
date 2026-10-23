# Data model

All data is **synthetic**. SQLite file: `app/data/bank.db`, created by `python -m app.data.seed`.

## 1. Customer profile (the object the rules engine reads)

| Field | Type | Notes |
|---|---|---|
| `customer_id` | str | e.g. `C001` |
| `name` | str | synthetic |
| `age` | int | |
| `gender` | `"F"|"M"|"O"` | |
| `state`, `district` | str | `"Karnataka"`, e.g. `"Mandya"` |
| `preferred_language` | `"kn"|"en"|"hi"` | |
| `has_savings_account` | bool | |
| `account_no_masked` | str \| null | `XXXXXX4521` |
| `aadhaar_masked` | str \| null | `XXXX-XXXX-1234`; never store the full number |
| `aadhaar_seeded` | bool \| null | |
| `ekyc_done` | bool \| null | |
| `occupation` | enum | `farmer`, `salaried`, `self_employed`, `gig_worker`, `homemaker`, `student`, `pensioner`, `govt_employee` |
| `is_income_tax_payer` | bool \| null | |
| `is_govt_employee` | bool \| null | serving or retired, non-Group D |
| `is_registered_professional` | bool \| null | |
| `monthly_pension` | int \| null | ₹; 0 if none |
| `is_landholder` | bool \| null | |
| `land_acres` | float \| null | display only |
| `grows_notified_crop` | bool \| null | |
| `in_notified_area` | bool \| null | |
| `girl_children_under_10` | int \| null | |
| `existing_ssy_accounts` | int \| null | |
| `enrollments` | list[str] | product IDs already held |
| `mobile_masked` | str \| null | `XXXXXX7788` |

`null` means *unknown*, and the rules engine treats it as missing. The DB stores what the bank already knows; the interview fills the rest.

## 2. Tables

```sql
CREATE TABLE customers (
  customer_id TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL,          -- the profile above
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  officer_id TEXT NOT NULL,
  state TEXT NOT NULL,                 -- INGESTING | EVALUATING | ASKING | PLANNING | ACTING | AWAITING_APPROVAL | REVISING | DONE
  profile_overrides_json TEXT DEFAULT '{}', -- facts learned in the interview
  questions_asked INTEGER DEFAULT 0,
  messages_json TEXT DEFAULT '[]',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE queue_items (
  item_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  customer_id TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- ENROLLMENT_FORM | SERVICE_REQUEST
  product_id TEXT,                     -- for ENROLLMENT_FORM
  action_id TEXT,                      -- for SERVICE_REQUEST (AADHAAR_SEEDING_REQUEST, ...)
  payload_json TEXT NOT NULL,          -- filled fields
  pdf_path TEXT,
  status TEXT NOT NULL,                -- DRAFT | PENDING_APPROVAL | APPROVED | REJECTED | REVISED | SUBMITTED | NEEDS_OFFICER
  reject_reason TEXT,
  revision_count INTEGER DEFAULT 0,
  decided_by TEXT,
  decided_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT CURRENT_TIMESTAMP,
  session_id TEXT,
  actor TEXT NOT NULL,                 -- agent | officer | system
  event TEXT NOT NULL,                 -- tool_call | tool_result | llm_reply | approve | reject | revise | error
  payload_json TEXT
);
```

Queue transitions (enforced in `app/queue.py`, any other transition raises):

```
DRAFT → PENDING_APPROVAL
PENDING_APPROVAL → APPROVED | REJECTED
REJECTED → REVISED → PENDING_APPROVAL     (revision_count += 1)
REJECTED → NEEDS_OFFICER                  (rejected again when revision_count is already 2)
APPROVED → SUBMITTED                       (mock submission, only from APPROVED)
```

## 3. Demo personas (seed exactly these)

| ID | Persona | Key facts | Expected outcome |
|---|---|---|---|
| `C001` | **Ramesh Gowda**, 38, farmer, Mandya, prefers Kannada | has account, **not Aadhaar-seeded**, eKYC not done, landholder 3 ac, grows notified ragi in notified area, not IT payer, no pension | PMKISAN_DBT `ELIGIBLE_BLOCKED` (seeding + eKYC first) · PMFBY `UNKNOWN` → one question → `ELIGIBLE` · PMJJBY, PMSBY, APY `ELIGIBLE` · SSY, PMJDY `NOT_ELIGIBLE` |
| `C002` | **Priya Nair**, 45, salaried, Bengaluru | has account, **IT payer**, 1 girl aged 6, 0 SSY accounts | PMJJBY, PMSBY, SSY `ELIGIBLE` · **APY `NOT_ELIGIBLE` (age + IT payer)** · PMJDY `NOT_ELIGIBLE` |
| `C003` | **Lakshmamma**, 62, homemaker, Tumakuru | **no savings account**, not IT payer | PMJDY `ELIGIBLE` → unlocks PMSBY (`ELIGIBLE_BLOCKED` until account opens) · PMJJBY, APY `NOT_ELIGIBLE` (age) |
| `C004` | **Arjun Kumar**, 24, gig worker, Bengaluru | has account, **IT status = null** | APY `UNKNOWN` → agent asks → becomes `ELIGIBLE` |
| `C005` | **Suresh Rao**, 66, retired govt clerk, now farms his own land | has account, `occupation` = `farmer`, landholder, pension ₹18,000/month, `is_govt_employee` true | PMKISAN_DBT `NOT_ELIGIBLE` (pension + govt) · PMSBY `ELIGIBLE` |
| `C006` | **Fathima Begum**, 29, self-employed tailor | has account, not IT payer, 2 girls (3 and 8), 1 existing SSY, already in PMSBY | SSY `ELIGIBLE` (second account) · PMSBY `ALREADY_ENROLLED` · PMJJBY, APY `ELIGIBLE` |

Every field not called out above is seeded with a known value (`false`, `0`, `[]`), so the only `null`s are the deliberate unknowns: Ramesh's crop fields and Arjun's IT status. A stray `null` would make an extra product `UNKNOWN` and change the expected outcomes.

Use `C001` and `C002` in the main demo; keep the others for judges' questions.

## 4. Seed data shape (example)

```json
{
  "customer_id": "C001",
  "name": "Ramesh Gowda",
  "age": 38, "gender": "M",
  "state": "Karnataka", "district": "Mandya",
  "preferred_language": "kn",
  "has_savings_account": true,
  "account_no_masked": "XXXXXX4521",
  "aadhaar_masked": "XXXX-XXXX-3391",
  "aadhaar_seeded": false,
  "ekyc_done": false,
  "occupation": "farmer",
  "is_income_tax_payer": false,
  "is_govt_employee": false,
  "is_registered_professional": false,
  "monthly_pension": 0,
  "is_landholder": true,
  "land_acres": 3.0,
  "grows_notified_crop": null,
  "in_notified_area": null,
  "girl_children_under_10": 0,
  "existing_ssy_accounts": 0,
  "enrollments": [],
  "mobile_masked": "XXXXXX7788"
}
```

`grows_notified_crop` and `in_notified_area` are deliberately `null` for Ramesh, so the agent has to ask one question that resolves PMFBY.
