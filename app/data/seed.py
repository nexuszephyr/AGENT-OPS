"""Create bank.db with the six demo personas plus 30 branch customers (docs/DATA_MODEL.md §3).

    python -m app.data.seed            # create / upsert customers
    python -m app.data.seed --reset    # wipe sessions, queue and audit too (run before every demo)

Every rule field is seeded with a known value; the only nulls are the deliberate
unknowns (Ramesh's crop fields and Arjun's income-tax status). Branch customers
(C101-C130, for the proactive scan) have no nulls at all.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3

from app import config, db

_DEFAULTS = {
    "state": "Karnataka",
    "preferred_language": "en",
    "has_savings_account": True,
    "aadhaar_seeded": True,
    "ekyc_done": True,
    "is_income_tax_payer": False,
    "is_govt_employee": False,
    "is_registered_professional": False,
    "monthly_pension": 0,
    "is_landholder": False,
    "land_acres": 0.0,
    "grows_notified_crop": False,
    "in_notified_area": False,
    "girl_children_under_10": 0,
    "existing_ssy_accounts": 0,
    "enrollments": [],
}

PERSONAS: list[dict] = [
    {
        "customer_id": "C001", "name": "Ramesh Gowda", "age": 38, "gender": "M",
        "district": "Mandya", "preferred_language": "kn",
        "account_no_masked": "XXXXXX4521", "aadhaar_masked": "XXXX-XXXX-3391",
        "mobile_masked": "XXXXXX7788",
        "aadhaar_seeded": False, "ekyc_done": False,
        "occupation": "farmer", "is_landholder": True, "land_acres": 3.0,
        # deliberate unknowns: the agent must ask one question to resolve PMFBY
        "grows_notified_crop": None, "in_notified_area": None,
    },
    {
        "customer_id": "C002", "name": "Priya Nair", "age": 45, "gender": "F",
        "district": "Bengaluru Urban",
        "account_no_masked": "XXXXXX9012", "aadhaar_masked": "XXXX-XXXX-5520",
        "mobile_masked": "XXXXXX3141",
        "occupation": "salaried", "is_income_tax_payer": True,
        "girl_children_under_10": 1, "existing_ssy_accounts": 0,
    },
    {
        "customer_id": "C003", "name": "Lakshmamma", "age": 62, "gender": "F",
        "district": "Tumakuru", "preferred_language": "kn",
        "has_savings_account": False, "account_no_masked": None,
        "aadhaar_masked": "XXXX-XXXX-8806", "mobile_masked": None,
        "aadhaar_seeded": False, "ekyc_done": False,
        "occupation": "homemaker",
    },
    {
        "customer_id": "C004", "name": "Arjun Kumar", "age": 24, "gender": "M",
        "district": "Bengaluru Urban",
        "account_no_masked": "XXXXXX6670", "aadhaar_masked": "XXXX-XXXX-2204",
        "mobile_masked": "XXXXXX5590",
        "occupation": "gig_worker",
        # deliberate unknown: APY starts UNKNOWN, agent asks Q_ITP
        "is_income_tax_payer": None,
    },
    {
        "customer_id": "C005", "name": "Suresh Rao", "age": 66, "gender": "M",
        "district": "Hassan",
        "account_no_masked": "XXXXXX1187", "aadhaar_masked": "XXXX-XXXX-7713",
        "mobile_masked": "XXXXXX2026",
        "occupation": "farmer", "is_landholder": True, "land_acres": 4.5,
        "is_govt_employee": True, "monthly_pension": 18000,
    },
    {
        "customer_id": "C006", "name": "Fathima Begum", "age": 29, "gender": "F",
        "district": "Mysuru",
        "account_no_masked": "XXXXXX3358", "aadhaar_masked": "XXXX-XXXX-4417",
        "mobile_masked": "XXXXXX8824",
        "occupation": "self_employed",
        "girl_children_under_10": 2, "existing_ssy_accounts": 1,
        "enrollments": ["PMSBY"],
    },
]


def build_profiles() -> list[dict]:
    return [{**_DEFAULTS, **p} for p in PERSONAS]


# --- branch customers (issue #37): a realistic book for the proactive scan ----------------------

_FIRST = {"M": ["Manjunath", "Basavaraj", "Shivakumar", "Nagaraj", "Mahesh", "Prakash", "Girish", "Siddappa",
                "Venkatesh", "Irfan", "Santosh", "Chandrashekar"],
          "F": ["Lakshmi", "Geetha", "Savitha", "Kavya", "Shobha", "Rekha", "Pushpa", "Nethra", "Ayesha",
                "Sunanda", "Bhagya", "Divya"]}
_LAST = ["Gowda", "Hegde", "Naik", "Patil", "Shetty", "Reddy", "Kumar", "Rao", "Murthy", "Swamy", "Khan", "Bhat"]
_DISTRICTS = ["Mandya", "Mysuru", "Hassan", "Tumakuru", "Chikkamagaluru", "Belagavi", "Dharwad", "Ballari",
              "Kolar", "Shivamogga", "Udupi", "Raichur"]
# 30 slots: 12 farmers (40%), then the rest of the branch
_OCCUPATIONS = (["farmer"] * 12 + ["salaried"] * 6 + ["gig_worker"] * 4 + ["homemaker"] * 4
                + ["self_employed"] * 4)
_AGES = {"farmer": (25, 68), "salaried": (23, 58), "gig_worker": (19, 35), "homemaker": (22, 60),
         "self_employed": (21, 55)}


def _masked(rng: random.Random, prefix: str) -> str:
    return f"{prefix}{rng.randint(0, 9999):04d}"


def _branch_customers() -> list[dict]:
    rng = random.Random(42)  # noqa: S311 - synthetic data, not crypto: same book on every reset
    occupations = list(_OCCUPATIONS)
    rng.shuffle(occupations)
    out, farmers_seen = [], 0
    for n, occ in enumerate(occupations):
        gender = "F" if occ == "homemaker" else rng.choice("MF")
        age = rng.randint(*_AGES[occ])
        c: dict = {
            "customer_id": f"C{101 + n}", "name": f"{rng.choice(_FIRST[gender])} {rng.choice(_LAST)}",
            "age": age, "gender": gender, "district": rng.choice(_DISTRICTS), "occupation": occ,
            "preferred_language": "kn" if rng.random() < 0.6 else "en",
            "aadhaar_masked": _masked(rng, "XXXX-XXXX-"), "mobile_masked": _masked(rng, "XXXXXX"),
            "account_no_masked": _masked(rng, "XXXXXX"),
        }
        if occ == "farmer":
            farmers_seen += 1
            c.update(is_landholder=rng.random() < 0.9, grows_notified_crop=rng.random() < 0.75,
                     in_notified_area=rng.random() < 0.75)
            c["land_acres"] = round(rng.uniform(0.5, 6.0), 1) if c["is_landholder"] else 0.0
            # a few unseeded / no-eKYC accounts: their PM-KISAN payments would fail
            if farmers_seen <= 3:
                c["aadhaar_seeded"] = False
            if farmers_seen in (3, 4):
                c["ekyc_done"] = False
            if age >= 60 and rng.random() < 0.5:
                c.update(is_govt_employee=True, monthly_pension=rng.choice([12000, 15000, 18000]))
        elif occ == "salaried":
            c["is_income_tax_payer"] = rng.random() < 0.7
        elif occ == "self_employed":
            c["is_income_tax_payer"] = rng.random() < 0.3
        if 22 <= age <= 45 and rng.random() < 0.35:
            c["girl_children_under_10"] = rng.choice([1, 1, 2])
            c["existing_ssy_accounts"] = rng.choice([0, 0, 1])
        held = [pid for pid, chance in (("PMSBY", 0.25), ("PMJJBY", 0.15)) if rng.random() < chance]
        if held:
            c["enrollments"] = held
        out.append(c)
    # 3 customers without a savings account (no account number, so no seeding / eKYC either)
    for c in [c for c in out if c["occupation"] in ("homemaker", "gig_worker")][:3]:
        del c["account_no_masked"]
        c.update(has_savings_account=False, aadhaar_seeded=False, ekyc_done=False, enrollments=[])
    return out


BRANCH_CUSTOMERS: list[dict] = _branch_customers()


def build_branch_profiles() -> list[dict]:
    return [{**_DEFAULTS, **c} for c in BRANCH_CUSTOMERS]


def seed(conn: sqlite3.Connection, reset: bool = False) -> None:
    if reset:
        conn.executescript(
            "DELETE FROM audit_log; DELETE FROM queue_items; DELETE FROM sessions; DELETE FROM customers;"
        )
        if config.OUT_DIR.exists():
            shutil.rmtree(config.OUT_DIR)
    # `segment` lets the UI keep the six demo personas apart from the branch book
    rows = [{**p, "segment": "demo"} for p in build_profiles()]
    rows += [{**p, "segment": "branch"} for p in build_branch_profiles()]
    for p in rows:
        conn.execute(
            "INSERT INTO customers (customer_id, profile_json) VALUES (?, ?) "
            "ON CONFLICT(customer_id) DO UPDATE SET profile_json = excluded.profile_json",
            (p["customer_id"], json.dumps(p, ensure_ascii=False)),
        )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="wipe sessions, queue and audit log")
    args = ap.parse_args()
    conn = db.connect()
    seed(conn, reset=args.reset)
    n = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    print(f"Seeded {n} customers into {config.DB_PATH}" + (" (reset)" if args.reset else ""))


if __name__ == "__main__":
    main()
