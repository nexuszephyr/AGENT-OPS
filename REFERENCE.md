# REFERENCE.md: product and domain reference

This is the business-domain reference for the 7 products the agent handles. `app/rules/products.json` is the machine-readable version of this file; keep the two in sync.

> **Verification status:** these rules are simplified from public sources for a prototype. Before the demo, check every threshold marked ⚠ against the official scheme page and update `last_verified` in `products.json`. Never present these as legal or financial advice.

## Glossary

| Term | Meaning |
|---|---|
| BC | Banking Correspondent: a bank's field agent who offers account and scheme services in villages |
| DBT | Direct Benefit Transfer: government pays benefits straight into a beneficiary's bank account |
| Aadhaar seeding | Linking a bank account to the customer's Aadhaar so DBT payments can reach it |
| eKYC | Electronic KYC verification using Aadhaar (OTP or biometric) |
| Auto-debit consent | Customer's mandate letting the bank debit the yearly premium/contribution |
| Notified crop / area | Crops and areas the state has notified as covered under PMFBY for a season |
| RTC / Pahani | Karnataka land record (Record of Rights, Tenancy and Crops) |
| IT payer | Person who paid income tax (relevant exclusion for APY and PM-KISAN) |

How the eligibility bullets map to `products.json`: age, income-tax and similar checks are rules; "has a savings account" (and seeding / eKYC for PM-KISAN) are **prerequisites** that give `ELIGIBLE_BLOCKED`; "not already enrolled" is the `ALREADY_ENROLLED` status; auto-debit consent is collected as the `AUTODEBIT_CONSENT` document, not checked as a rule.

## Product summary

| ID | Product | Type | Delivered by bank? |
|---|---|---|---|
| `PMJDY` | Pradhan Mantri Jan Dhan Yojana | Basic savings account | Yes, opened at branch / BC |
| `PMJJBY` | PM Jeevan Jyoti Bima Yojana | Life insurance | Yes, enrolled via savings account |
| `PMSBY` | PM Suraksha Bima Yojana | Accident insurance | Yes, enrolled via savings account |
| `APY` | Atal Pension Yojana | Pension | Yes, enrolled via savings account |
| `SSY` | Sukanya Samriddhi Yojana | Girl-child savings | Yes, authorised banks and post offices |
| `PMKISAN_DBT` | PM-KISAN DBT readiness | Income support (DBT) | Bank does Aadhaar seeding + eKYC; registration is on the PM-KISAN portal / CSC |
| `PMFBY` | PM Fasal Bima Yojana | Crop insurance | Yes, banks enrol loanee and non-loanee farmers |

---

## 1. PMJDY: Jan Dhan account

- **Benefit:** zero-balance basic savings account, RuPay debit card (with accident insurance cover), overdraft facility for eligible account holders.
- **Indicative value:** none (`value_inr: null`, "savings account (no single value)"). The card's accident cover is a talking point, not counted.
- **Eligibility (prototype rules):**
  - Does **not** already hold a savings account (`has_savings_account == false`)
  - Age ≥ 10 ⚠ (minors 10+ can open with guardian)
- **Documents:** Aadhaar (masked), photograph. Simplified KYC allowed if no officially valid documents.
- **Why it matters in the planner:** it is a **prerequisite** for PMJJBY, PMSBY and APY. If the customer has no account, the agent plans PMJDY first.

## 2. PMJJBY: life cover

- **Benefit:** ₹2,00,000 life cover (death due to any cause) for a premium of **₹436 per year**, auto-debited.
- **Indicative value:** ₹2,00,000 life cover (`value_inr: 200000`).
- **Eligibility:**
  - Age **18–50** at enrollment ⚠ (cover continues to 55)
  - Has a savings account
  - Gives auto-debit consent
  - Not already enrolled
- **Documents:** consent-cum-declaration form, nominee details, Aadhaar (masked) as KYC.
- **Notes:** new enrollees have a lien period (claims for non-accidental death in the first 30 days are not paid) ⚠. Show this in the explanation, not as a rule.

## 3. PMSBY: accident cover

- **Benefit:** ₹2,00,000 for accidental death or total permanent disability; ₹1,00,000 for partial permanent disability. Premium **₹20 per year** ⚠.
- **Indicative value:** ₹2,00,000 accident cover (`value_inr: 200000`).
- **Eligibility:**
  - Age **18–70** ⚠
  - Has a savings account
  - Gives auto-debit consent
  - Not already enrolled
- **Documents:** consent-cum-declaration form (auto-debit consent), nominee details, Aadhaar (masked) as KYC.

## 4. APY: Atal Pension Yojana

- **Benefit:** guaranteed monthly pension of ₹1,000–₹5,000 from age 60, depending on contribution; contribution amount depends on joining age.
- **Indicative value:** ₹12,000 per year, the minimum guaranteed pension (₹1,000/month) (`value_inr: 12000`).
- **Eligibility:**
  - Age **18–40** ⚠
  - Has a savings account
  - **Not an income-tax payer** (exclusion effective from 1 Oct 2022) ⚠
  - Not already enrolled
- **Documents:** APY registration form (includes auto-debit consent), nominee details, Aadhaar (masked), mobile number.
- **Demo trap:** Priya (45, IT payer) fails on **both** age and IT status. The explanation must list both rules.

## 5. SSY: Sukanya Samriddhi Yojana

- **Benefit:** high-interest, tax-advantaged savings account for a girl child.
- **Indicative value:** none (`value_inr: null`): it depends on deposits.
- **Eligibility:**
  - Customer is parent/guardian of a **girl child below 10 years** ⚠
  - Family has fewer than **2** existing SSY accounts ⚠ (third allowed only for twins/triplets: out of scope, mark UNKNOWN → verify manually)
- **Documents:** girl child's birth certificate, guardian's KYC (Aadhaar masked, photograph), address proof.
- **Deposit limits:** min ₹250, max ₹1,50,000 per financial year ⚠ (display only).

## 6. PMKISAN_DBT: PM-KISAN DBT readiness

The bank does not register farmers for PM-KISAN; it makes sure the farmer's account can **receive** the DBT. We model this as "DBT readiness".

- **Benefit:** ₹6,000 per year in three instalments of ₹2,000 to landholding farmer families.
- **Indicative value:** ₹6,000 income support per year (`value_inr: 6000`).
- **Eligibility:**
  - Occupation is farmer **and** owns cultivable land (`is_landholder == true`)
  - **Exclusions** ⚠: income-tax payers; serving or retired government employees (except Group D / MTS); pensioners with monthly pension ≥ ₹10,000; registered professionals (doctors, engineers, lawyers, CAs, architects); constitutional post holders; institutional landholders. The prototype rules cover the first four; constitutional post holders and institutional landholders are not modelled (out of scope for branch walk-ins).
- **Readiness checks (become actions, not eligibility):**
  - `aadhaar_seeded == true`; if false → action `AADHAAR_SEEDING_REQUEST`
  - `ekyc_done == true`; if false → action `EKYC_REQUEST`
- **Documents:** Aadhaar (masked), land record (RTC), bank passbook.
- **Demo trap:** Ramesh is eligible, but his account isn't seeded, so the agent plans seeding **first** because it unlocks DBT.

## 7. PMFBY: crop insurance

- **Benefit:** insurance against crop loss from natural risks. Farmer premium ⚠: 2% (kharif), 1.5% (rabi), 5% (commercial/horticulture).
- **Indicative value:** none (`value_inr: null`): it depends on the sum insured.
- **Eligibility:**
  - Occupation is farmer (owner, tenant or sharecropper)
  - Grows a **notified crop** in a **notified area** for the current season
  - Has a bank account
  - Enrollment is voluntary (since kharif 2020) ⚠
- **Documents:** land record (RTC) or tenancy/sowing certificate, Aadhaar (masked), bank passbook, sowing declaration.

---

## Prerequisite and dependency map

Used by `app/planner/documents.py`.

```
OPEN_SAVINGS_ACCOUNT (PMJDY) ──► PMJJBY
                             ──► PMSBY
                             ──► APY
                             ──► PMFBY
                             ──► PMKISAN_DBT
AADHAAR_SEEDING_REQUEST ──► PMKISAN_DBT
EKYC_REQUEST            ──► PMKISAN_DBT
```

Planner priority: perform the prerequisite that **unlocks the most eligible products** first; ties are broken by the fewest documents needed.

## Document catalogue

| Doc ID | Name | Used by |
|---|---|---|
| `AADHAAR` | Aadhaar (masked copy) | PMJDY, APY, SSY, PMKISAN_DBT, PMFBY, PMJJBY, PMSBY |
| `PHOTO` | Passport-size photograph | PMJDY, SSY |
| `NOMINEE` | Nominee details | PMJJBY, PMSBY, APY |
| `AUTODEBIT_CONSENT` | Auto-debit consent form | PMJJBY, PMSBY, APY |
| `GIRL_BIRTH_CERT` | Girl child's birth certificate | SSY |
| `ADDRESS_PROOF` | Address proof | SSY |
| `RTC` | Land record (RTC / Pahani) | PMKISAN_DBT, PMFBY |
| `SOWING_DECL` | Sowing declaration / certificate | PMFBY |
| `PASSBOOK` | Bank passbook copy | PMKISAN_DBT, PMFBY |
| `MOBILE` | Registered mobile number | APY |

## Sources

Record the exact page used for each product in `products.json → source_url`. Starting points:

- myScheme national platform (NeGD / MeitY): https://www.myscheme.gov.in
- Jan Dhan: https://pmjdy.gov.in
- Jan Suraksha (PMJJBY / PMSBY / APY): https://jansuraksha.gov.in
- PM-KISAN: https://pmkisan.gov.in
- PMFBY: https://pmfby.gov.in
- Sukanya Samriddhi (India Post / NSI): https://www.nsiindia.gov.in
- Bank summary of government-sponsored schemes (Nainital Bank): https://www.nainitalbank.bank.in/english/Government_Sponsored_Schemes.aspx
- PMJJBY ₹436 premium summary: https://anantamias.com/pm-jeevan-jyoti-bima-yojana/?pdf=1
