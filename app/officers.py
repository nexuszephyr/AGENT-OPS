"""Officers and their roles (maker-checker, issue #39).

The maker (banking correspondent) prepares enrollments; only a checker (branch
manager) who did not prepare an item may approve or reject it (see app/queue.py).
"""
from __future__ import annotations

MAKER, CHECKER = "maker", "checker"

OFFICERS: dict[str, dict[str, str]] = {
    "officer-1": {"name": "Banking Correspondent", "role": MAKER},
    "manager-1": {"name": "Branch Manager", "role": CHECKER},
}


def get_officer(officer_id: str | None) -> dict[str, str] | None:
    o = OFFICERS.get(officer_id or "")
    return {"officer_id": officer_id, **o} if o and officer_id else None


def is_checker(officer_id: str | None) -> bool:
    o = OFFICERS.get(officer_id or "")
    return o is not None and o["role"] == CHECKER
