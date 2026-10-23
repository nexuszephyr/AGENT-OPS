"""Create bank.db with the six synthetic demo customers (docs/DATA_MODEL.md §3).

    python -m app.data.seed            # create / upsert customers
    python -m app.data.seed --reset    # wipe sessions, queue and audit too (run before every demo)

Every rule field is seeded with a known value; the only nulls are the deliberate
unknowns (Ramesh's crop fields and Arjun's income-tax status).
"""
from __future__ import annotations

import argparse
import json
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


def seed(conn: sqlite3.Connection, reset: bool = False) -> None:
    if reset:
        conn.executescript(
            "DELETE FROM audit_log; DELETE FROM queue_items; DELETE FROM sessions; DELETE FROM customers;"
        )
        if config.OUT_DIR.exists():
            shutil.rmtree(config.OUT_DIR)
    for p in build_profiles():
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
