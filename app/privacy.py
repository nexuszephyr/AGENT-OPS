"""Identifier checks shared by every path that writes a payload (AGENTS.md rule 5).

One helper, used by form building, queue creation and revisions, so the paths
can't drift apart.
"""
from __future__ import annotations

import re
from typing import Any

# 12 digits, optionally grouped 4-4-4 with space, dot or hyphen; not part of a longer number.
FULL_AADHAAR = re.compile(r"(?<!\d)\d{4}[\s.-]?\d{4}[\s.-]?\d{4}(?!\d)")


class UnmaskedIdentifierError(ValueError):
    pass


def find_full_ids(obj: Any, path: str = "") -> list[str]:
    """Return the paths of every string value (recursively) that looks like a full Aadhaar number."""
    hits: list[str] = []
    if isinstance(obj, str):
        if FULL_AADHAAR.search(obj):
            hits.append(path or "<value>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            hits += find_full_ids(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits += find_full_ids(v, f"{path}[{i}]")
    elif isinstance(obj, int) and not isinstance(obj, bool) and len(str(abs(obj))) == 12:
        hits.append(path or "<value>")
    return hits


def _mask(m: re.Match) -> str:
    return "XXXX-XXXX-" + re.sub(r"\D", "", m.group(0))[-4:]


def redact_full_ids(obj: Any) -> Any:
    """Copy of obj with every full-Aadhaar-looking value masked. Used before anything is logged."""
    if isinstance(obj, str):
        return FULL_AADHAAR.sub(_mask, obj)
    if isinstance(obj, dict):
        return {k: redact_full_ids(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_full_ids(v) for v in obj]
    if isinstance(obj, int) and not isinstance(obj, bool) and len(str(abs(obj))) == 12:
        return "XXXX-XXXX-" + str(abs(obj))[-4:]
    return obj


def assert_no_full_ids(obj: Any) -> None:
    hits = find_full_ids(obj)
    if hits:
        raise UnmaskedIdentifierError(
            f"{', '.join(hits)} looks like a full Aadhaar number; only masked IDs (XXXX-XXXX-1234) are allowed")
