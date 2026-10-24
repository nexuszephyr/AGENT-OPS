# Rules spec: `app/rules/products.json` and `app/rules/engine.py`

The rules engine is the **only** component allowed to decide eligibility. It is pure Python, uses no LLM and does no I/O, and it is fully unit-tested.

## 1. Status values

| Status | Meaning | UI colour |
|---|---|---|
| `ELIGIBLE` | All rules pass and all prerequisites are met: ready to enrol | dark brown |
| `ELIGIBLE_BLOCKED` | Rules pass, but a prerequisite action is pending (e.g. no account, not Aadhaar-seeded) | amber |
| `NOT_ELIGIBLE` | At least one rule definitively fails | taupe, strikethrough |
| `UNKNOWN` | No rule fails, but a needed field is missing | grey |
| `ALREADY_ENROLLED` | Customer already has this product | outline |
| `VERIFY_MANUALLY` | Still UNKNOWN after the question limit, or an edge case out of scope | outline, warning icon |

## 2. Condition grammar

A condition is one of:

```jsonc
// leaf
{ "id": "R-APY-AGE-MAX", "field": "age", "op": "<=", "value": 40,
  "text": "Age must be 40 or below at enrollment" }

// combinators
{ "all": [ <cond>, ... ] }
{ "any": [ <cond>, ... ] }
{ "not": <cond>, "id": "...", "text": "..." }
```

Supported `op` values: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `is_true`, `is_false`.

Every leaf **must** have a unique `id` and a human-readable `text`. These are shown to the officer as the reason.

## 3. Three-valued evaluation (Kleene logic)

A field is *missing* if its key is absent or `null` in the profile.

| Node | Result |
|---|---|
| leaf, field missing | `U` (unknown); record the field in `unknown_fields` |
| leaf, field present | `T` or `F`; on `F` record the leaf in `failed_rules` |
| `all` | `F` if any child is `F`; else `U` if any child is `U`; else `T` |
| `any` | `T` if any child is `T`; else `U` if any child is `U`; else `F` |
| `not` | `T`↔`F`, `U` stays `U` |

Product status:
1. If `product_id` is in `profile.enrollments` → `ALREADY_ENROLLED`.
2. Evaluate `rules`: `F` → `NOT_ELIGIBLE`; `U` → `UNKNOWN`.
3. If `T`: evaluate each `prerequisites` entry. If any one is unmet → `ELIGIBLE_BLOCKED` (listing `blocked_by` actions for every unmet one). Otherwise, if any prerequisite field is missing → `UNKNOWN`. As with rules, a known blocker wins over a missing field.
4. Otherwise → `ELIGIBLE`.

**Important:** when a rule fails, report **all** failed leaves, not just the first (so Priya's APY rejection shows both age and IT status). Evaluate every child; don't short-circuit.

## 4. Result format

```json
{
  "product_id": "APY",
  "status": "NOT_ELIGIBLE",
  "failed_rules": [
    {"id": "R-APY-AGE-MAX", "text": "Age must be 40 or below at enrollment"},
    {"id": "R-APY-NOT-ITP", "text": "Income-tax payers cannot join APY (from 1 Oct 2022)"}
  ],
  "passed_rules": ["R-APY-AGE-MIN"],
  "unknown_fields": [],
  "blocked_by": [],
  "required_docs": ["AADHAAR", "NOMINEE", "AUTODEBIT_CONSENT", "MOBILE"],
  "source_url": "https://utilities.cept.gov.in/dop/pdfbind.ashx?id=7217",
  "last_verified": "2026-10-24",
  "value_inr": 12000,
  "value_basis": "minimum guaranteed pension per year (₹1,000/month)"
}
```

`value_inr` / `value_basis` are copied from the product (#36). They are **indicative**: the cover amount or annual benefit, never money paid out. `value_inr` is `null` when there's no single number (PMJDY, SSY, PMFBY). Sums skip nulls rather than counting them as ₹0. The value is metadata only: it never changes a status.

## 5. Startup validation

`engine.load_products()` must fail loudly if:
- any leaf lacks `id` or `text`
- rule IDs are duplicated
- a product lacks `source_url` or `last_verified`
- an `op` is not in the supported list
- a `required_docs` entry isn't in the document catalogue
- `value_inr` is missing, or is not an int ≥ 0 or `null` (a bool, float, string or negative number is rejected)
- `value_basis` is missing or empty

## 6. `products.json` (verified 2026-10-24 against the sources in REFERENCE.md)

```json
{
  "version": "2026-10-24",
  "documents": {
    "AADHAAR": "Aadhaar (masked copy)",
    "PHOTO": "Passport-size photograph",
    "NOMINEE": "Nominee details",
    "AUTODEBIT_CONSENT": "Auto-debit consent form",
    "GIRL_BIRTH_CERT": "Girl child's birth certificate",
    "ADDRESS_PROOF": "Address proof",
    "RTC": "Land record (RTC / Pahani)",
    "SOWING_DECL": "Sowing declaration / certificate",
    "PASSBOOK": "Bank passbook copy",
    "MOBILE": "Registered mobile number"
  },
  "actions": {
    "OPEN_SAVINGS_ACCOUNT": {"label": "Open Jan Dhan savings account", "product": "PMJDY"},
    "AADHAAR_SEEDING_REQUEST": {"label": "Seed account with Aadhaar for DBT"},
    "EKYC_REQUEST": {"label": "Complete Aadhaar eKYC"}
  },
  "products": [
    {
      "id": "PMJDY",
      "name": "Pradhan Mantri Jan Dhan Yojana",
      "category": "account",
      "benefit": "Zero-balance savings account with RuPay card",
      "value_inr": null,
      "value_basis": "savings account (no single value)",
      "rules": {"all": [
        {"id": "R-PMJDY-NO-ACCT", "field": "has_savings_account", "op": "is_false",
         "text": "Customer must not already hold a savings account"},
        {"id": "R-PMJDY-AGE-MIN", "field": "age", "op": ">=", "value": 10,
         "text": "Age must be 10 or above (minimum for opening through a Banking Correspondent with eKYC)"}
      ]},
      "prerequisites": [],
      "required_docs": ["AADHAAR", "PHOTO"],
      "source_url": "https://sbi.bank.in/web/faq-s/faq-pradhan-mantri-jan-dhan-yojana-pmjdy",
      "last_verified": "2026-10-24"
    },
    {
      "id": "PMJJBY",
      "name": "PM Jeevan Jyoti Bima Yojana",
      "category": "insurance",
      "benefit": "₹2 lakh life cover for ₹436 per year",
      "value_inr": 200000,
      "value_basis": "life cover",
      "rules": {"all": [
        {"id": "R-PMJJBY-AGE-MIN", "field": "age", "op": ">=", "value": 18, "text": "Age must be 18 or above"},
        {"id": "R-PMJJBY-AGE-MAX", "field": "age", "op": "<=", "value": 50, "text": "Age must be 50 or below at enrollment"}
      ]},
      "prerequisites": [
        {"field": "has_savings_account", "op": "is_true", "action": "OPEN_SAVINGS_ACCOUNT"}
      ],
      "required_docs": ["AADHAAR", "NOMINEE", "AUTODEBIT_CONSENT"],
      "source_url": "https://bankofmaharashtra.bank.in/pm-jeevan-jyoti-bima-yojana",
      "last_verified": "2026-10-24"
    },
    {
      "id": "PMSBY",
      "name": "PM Suraksha Bima Yojana",
      "category": "insurance",
      "benefit": "₹2 lakh accident cover for ₹20 per year",
      "value_inr": 200000,
      "value_basis": "accident cover",
      "rules": {"all": [
        {"id": "R-PMSBY-AGE-MIN", "field": "age", "op": ">=", "value": 18, "text": "Age must be 18 or above"},
        {"id": "R-PMSBY-AGE-MAX", "field": "age", "op": "<=", "value": 70, "text": "Age must be 70 or below"}
      ]},
      "prerequisites": [
        {"field": "has_savings_account", "op": "is_true", "action": "OPEN_SAVINGS_ACCOUNT"}
      ],
      "required_docs": ["AADHAAR", "NOMINEE", "AUTODEBIT_CONSENT"],
      "source_url": "https://bankofmaharashtra.bank.in/pm-suraksha-bima-yojana",
      "last_verified": "2026-10-24"
    },
    {
      "id": "APY",
      "name": "Atal Pension Yojana",
      "category": "pension",
      "benefit": "Guaranteed pension of ₹1,000–₹5,000 per month from age 60",
      "value_inr": 12000,
      "value_basis": "minimum guaranteed pension per year (₹1,000/month)",
      "rules": {"all": [
        {"id": "R-APY-AGE-MIN", "field": "age", "op": ">=", "value": 18, "text": "Age must be 18 or above"},
        {"id": "R-APY-AGE-MAX", "field": "age", "op": "<=", "value": 40, "text": "Age must be 40 or below at enrollment"},
        {"id": "R-APY-NOT-ITP", "field": "is_income_tax_payer", "op": "is_false",
         "text": "Income-tax payers cannot join APY (from 1 Oct 2022)"}
      ]},
      "prerequisites": [
        {"field": "has_savings_account", "op": "is_true", "action": "OPEN_SAVINGS_ACCOUNT"}
      ],
      "required_docs": ["AADHAAR", "NOMINEE", "AUTODEBIT_CONSENT", "MOBILE"],
      "source_url": "https://utilities.cept.gov.in/dop/pdfbind.ashx?id=7217",
      "last_verified": "2026-10-24"
    },
    {
      "id": "SSY",
      "name": "Sukanya Samriddhi Yojana",
      "category": "savings",
      "benefit": "Tax-advantaged savings account for a girl child",
      "value_inr": null,
      "value_basis": "depends on deposits",
      "rules": {"all": [
        {"id": "R-SSY-GIRL", "field": "girl_children_under_10", "op": ">=", "value": 1,
         "text": "Must be parent/guardian of a girl child below 10 years"},
        {"id": "R-SSY-MAX2", "field": "existing_ssy_accounts", "op": "<", "value": 2,
         "text": "A family may hold at most 2 SSY accounts"}
      ]},
      "prerequisites": [],
      "required_docs": ["GIRL_BIRTH_CERT", "AADHAAR", "PHOTO", "ADDRESS_PROOF"],
      "source_url": "https://IndiaPost.gov.in/VAS/DOP_PDFFiles/Savings%20Bank/Sukanya%20Samriddhi%20Account%20Scheme%202019%20English.pdf",
      "last_verified": "2026-10-24"
    },
    {
      "id": "PMKISAN_DBT",
      "name": "PM-KISAN DBT readiness",
      "category": "dbt",
      "benefit": "₹6,000 per year in three instalments to landholding farmer families",
      "value_inr": 6000,
      "value_basis": "income support per year",
      "rules": {"all": [
        {"id": "R-PMK-FARMER", "field": "occupation", "op": "==", "value": "farmer", "text": "Must be a farmer"},
        {"id": "R-PMK-LAND", "field": "is_landholder", "op": "is_true", "text": "Must own cultivable land"},
        {"id": "R-PMK-NOT-ITP", "field": "is_income_tax_payer", "op": "is_false", "text": "Income-tax payers are excluded"},
        {"id": "R-PMK-NOT-GOVT", "field": "is_govt_employee", "op": "is_false",
         "text": "Serving or retired government employees are excluded (except Group D / MTS)"},
        {"id": "R-PMK-NOT-PROF", "field": "is_registered_professional", "op": "is_false",
         "text": "Registered professionals (doctor, engineer, lawyer, CA, architect) are excluded"},
        {"id": "R-PMK-PENSION", "field": "monthly_pension", "op": "<", "value": 10000,
         "text": "Pensioners receiving ₹10,000 or more per month are excluded"}
      ]},
      "prerequisites": [
        {"field": "has_savings_account", "op": "is_true", "action": "OPEN_SAVINGS_ACCOUNT"},
        {"field": "aadhaar_seeded", "op": "is_true", "action": "AADHAAR_SEEDING_REQUEST"},
        {"field": "ekyc_done", "op": "is_true", "action": "EKYC_REQUEST"}
      ],
      "required_docs": ["AADHAAR", "RTC", "PASSBOOK"],
      "source_url": "https://fw.pmkisan.gov.in/Documents/FAQPMKISAN.pdf",
      "last_verified": "2026-10-24"
    },
    {
      "id": "PMFBY",
      "name": "PM Fasal Bima Yojana",
      "category": "insurance",
      "benefit": "Crop insurance against natural risks (farmer premium 2% kharif / 1.5% rabi / 5% commercial)",
      "value_inr": null,
      "value_basis": "depends on sum insured",
      "rules": {"all": [
        {"id": "R-PMFBY-FARMER", "field": "occupation", "op": "==", "value": "farmer",
         "text": "Must be a farmer (owner, tenant or sharecropper)"},
        {"id": "R-PMFBY-CROP", "field": "grows_notified_crop", "op": "is_true",
         "text": "Must grow a crop notified for this season"},
        {"id": "R-PMFBY-AREA", "field": "in_notified_area", "op": "is_true",
         "text": "Land must be in a notified area"}
      ]},
      "prerequisites": [
        {"field": "has_savings_account", "op": "is_true", "action": "OPEN_SAVINGS_ACCOUNT"}
      ],
      "required_docs": ["AADHAAR", "RTC", "SOWING_DECL", "PASSBOOK"],
      "source_url": "https://auccb.bank.in/pmfby/",
      "last_verified": "2026-10-24"
    }
  ]
}
```

## 7. Required unit tests (`tests/test_rules.py`)

| Test | Profile | Expect |
|---|---|---|
| `test_priya_apy_rejected_two_reasons` | age 45, IT payer | APY `NOT_ELIGIBLE`, failed = {AGE-MAX, NOT-ITP} |
| `test_priya_pmjjby_eligible` | age 45, has account | PMJJBY `ELIGIBLE` |
| `test_ramesh_pmkisan_blocked_on_seeding` | farmer, landholder, not seeded | PMKISAN_DBT `ELIGIBLE_BLOCKED`, blocked_by includes `AADHAAR_SEEDING_REQUEST` |
| `test_missing_it_status_is_unknown` | IT status null | APY `UNKNOWN`, unknown_fields = [is_income_tax_payer] |
| `test_unknown_does_not_mask_failure` | age 55, IT null | APY `NOT_ELIGIBLE` (F wins over U) |
| `test_no_account_blocks_insurance` | has_savings_account false, age 30 | PMJDY `ELIGIBLE`; PMJJBY `ELIGIBLE_BLOCKED` by OPEN_SAVINGS_ACCOUNT |
| `test_already_enrolled` | enrollments ["PMSBY"] | PMSBY `ALREADY_ENROLLED` |
| `test_pensioner_excluded_from_pmkisan` | monthly_pension 18000 | PMKISAN_DBT `NOT_ELIGIBLE` |
| `test_ssy_limit` | existing_ssy_accounts 2 | SSY `NOT_ELIGIBLE` |
| `test_validation_rejects_missing_source` | product without source_url | load raises |
