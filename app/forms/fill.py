"""Pre-filled enrollment forms and service requests as PDFs (reportlab).

Only masked identifiers ever reach a form (AGENTS.md rule 5).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from app import config
from app.privacy import assert_no_full_ids

# Extra fields the officer/customer must supply per product (not in the profile).
MANDATORY_EXTRA: dict[str, list[str]] = {
    "PMJJBY": ["nominee_name", "nominee_relation"],
    "PMSBY": ["nominee_name", "nominee_relation"],
    "APY": ["nominee_name", "nominee_relation", "pension_amount"],
    "SSY": ["girl_child_name", "girl_child_dob"],
    "PMFBY": ["crop_name", "season"],
}

PROFILE_FIELDS = [
    "customer_id", "name", "age", "gender", "district", "state",
    "account_no_masked", "aadhaar_masked", "mobile_masked", "occupation",
]

def build_payload(product_id: str | None, profile: dict, extra: dict | None = None,
                  action_id: str | None = None) -> tuple[dict, list[str]]:
    """Return (payload, missing_mandatory). Refuses unmasked 12-digit numbers anywhere in `extra`."""
    extra = dict(extra or {})
    assert_no_full_ids(extra)
    payload = {k: profile.get(k) for k in PROFILE_FIELDS}
    payload["form"] = product_id or action_id
    payload["prepared_on"] = date.today().isoformat()
    for k in MANDATORY_EXTRA.get(product_id or "", []):
        payload[k] = extra.get(k)
    for k, v in extra.items():
        payload.setdefault(k, v)
    missing = [k for k in MANDATORY_EXTRA.get(product_id or "", []) if not payload.get(k)]
    return payload, missing


def render_pdf(item_id: str, title: str, payload: dict, out_dir: Path | None = None) -> str:
    """Write a simple one-page PDF with a DRAFT watermark. Returns the file path."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    out_dir = out_dir or config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{item_id}.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4

    c.saveState()
    c.setFont("Helvetica-Bold", 42)
    c.setFillGray(0.88)
    c.translate(w / 2, h / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "DRAFT - pending officer approval")
    c.restoreState()

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, h - 60, title)
    c.setFont("Helvetica", 9)
    c.drawString(50, h - 76, f"Queue item {item_id} · Yojana Saathi prototype · synthetic data")
    y = h - 110
    c.setFont("Helvetica", 11)
    for k, v in payload.items():
        label = k.replace("_", " ").capitalize()
        c.drawString(50, y, f"{label}:")
        c.drawString(220, y, "" if v is None else str(v))
        y -= 20
        if y < 80:
            c.showPage()
            y = h - 60
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 40, "Not valid until approved by a bank officer. Identifiers are masked.")
    c.save()
    return str(path)
