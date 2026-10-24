"""UI polish and accessibility pass (#24): no raw JSON, readable errors, status never colour alone, approval chain."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

import components as ui  # noqa: E402

NAMES = {"officer-1": "Banking Correspondent", "manager-1": "Branch Manager"}


def name(oid):
    return NAMES.get(oid or "", oid or "unknown")


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


# --- no raw JSON anywhere in the UI ---------------------------------------------------------

def test_the_ui_never_renders_raw_json():
    """Done-when of #24: no raw JSON. Payloads go through key_values / readable instead."""
    for f in ("ui/components.py", "ui/streamlit_app.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "st.json(" not in src, f
        assert "json.dumps(" not in src, f


@pytest.mark.parametrize(("value", "text"), [
    (True, "yes"), (False, "no"), (None, "—"), ("", "—"), ([], "—"), (["a", "b"], "a, b"), (42, "42"),
    ({"customer_id": "C001", "officer_id": "officer-1"}, "Customer ID: C001 · Officer ID: officer-1"),
    ({"tool": "fill_form", "args": {"product_id": "SSY"}}, "Tool: fill_form · Args: (Product ID: SSY)"),
    ({"aadhaar_seeded": True}, "Aadhaar seeded: yes"),
])
def test_readable_turns_any_payload_into_one_plain_line(value, text):
    assert ui.readable(value) == text
    assert not re.search(r'[{}"]', ui.readable(value))


def test_readable_truncates_long_values():
    out = ui.readable({"reason": "x" * 400}, limit=50)
    assert len(out) == 50 and out.endswith("…")


@pytest.mark.parametrize(("field", "text"), [
    ("nominee_name", "Nominee name"), ("customer_id", "Customer ID"), ("llm", "LLM"), ("ekyc_done", "eKYC done"),
    ("girl_child_dob", "Girl child date of birth"), ("pdf_path", "PDF path"), ("session_start", "Session start"),
    ("ASKING", "Asking"), ("AWAITING_APPROVAL", "Awaiting approval"), ("LLM", "LLM"),  # session stages read as words
])
def test_field_labels_keep_acronyms(field, text):
    assert ui.field_label(field) == text


def test_key_values_render_nested_values_as_text_and_escape(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.key_values({"changes": {"nominee_name": "<b>Anjali</b>"}, "ok": True})
    html = out[0]
    assert "Nominee name: &lt;b&gt;Anjali&lt;/b&gt;" in html and "<dd>yes</dd>" in html and "{" not in html


# --- API errors: a sentence, never the response body ----------------------------------------

@pytest.mark.parametrize(("status", "body", "text"), [
    (400, '{"detail":"no session S-1"}', "No session S-1."),
    (403, '{"detail":"officer-1 prepared Q-1, so they cannot approve it"}',
     "Officer-1 prepared Q-1, so they cannot approve it."),
    (422, '{"detail":[{"type":"missing","loc":["body","customer_id"],"msg":"Field required","input":{}}]}',
     "Customer ID: field required."),
    (500, "Internal Server Error", "The API hit an unexpected error (500). Check the API log, then try again."),
    (404, '{"detail":""}', "The API refused this request (404)."),
])
def test_api_errors_read_as_a_sentence(status, body, text):
    out = ui.api_error_text(status, body)
    assert out == text and "{" not in out


def test_act_on_an_unknown_session_is_a_400_not_a_crash(client):
    r = TestClient(main.app, raise_server_exceptions=False).post("/sessions/S-NOPE/act", json={})
    assert r.status_code == 400 and r.json()["detail"] == "no session S-NOPE"


# --- status is never colour alone -------------------------------------------------------------

def test_attention_tiles_carry_an_icon_and_an_accessible_name(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.kpi_tiles([("Calm", 1), ("Escalated", 2, True)])
    calm, hot = re.split(r'<div class="ys-kpi(?: ys-kpi-attention)?">', out[0])[1:]
    assert "ys-kpi-flag" not in calm
    assert 'aria-label="Needs attention"' in hot and ">!</span>" in hot


# --- approval chain (maker-checker, visible on every card) ------------------------------------

def test_chain_for_a_pending_item_names_the_maker_and_who_it_waits_on():
    html = ui.approval_chain_html({"status": "PENDING_APPROVAL", "prepared_by": "officer-1", "revision_count": 1},
                                  name)
    assert "✓</span>Prepared by Banking Correspondent" in html
    assert "Waiting on Branch Manager · revision 1" in html
    assert "ys-chain-next" in html and "Submit" in html and 'aria-label="Approval chain"' in html


def test_chain_for_a_rejected_and_a_submitted_item():
    back = ui.approval_chain_html({"status": "REJECTED", "prepared_by": "officer-1", "decided_by": "manager-1"}, name)
    assert "✕</span>Sent back by Branch Manager" in back
    done = ui.approval_chain_html({"status": "SUBMITTED", "prepared_by": "officer-1", "decided_by": "manager-1"}, name)
    assert "Approved by Branch Manager" in done and "Submitted (mock)" in done and "ys-chain-next" not in done


def test_chain_escapes_names():
    html = ui.approval_chain_html({"status": "PENDING_APPROVAL", "prepared_by": "x"}, lambda _: "<img src=x>")
    assert "<img" not in html and "&lt;img src=x&gt;" in html


# --- Prepare all steps: plain inputs from the plan ---------------------------------------------

def test_prep_fields_asks_a_shared_detail_once_and_skips_blocked_steps(client):
    st = client.post("/sessions", json={"customer_id": "C001"}).json()
    needed = ui.prep_fields(st)
    assert needed["nominee_name"] == ["PMJJBY", "PMSBY", "APY"]
    assert "pension_amount" in needed and not any("PMKISAN" in p for ps in needed.values() for p in ps)


def test_prepare_with_form_values_fills_every_form_that_shares_them(client):
    st = client.post("/sessions", json={"customer_id": "C001"}).json()
    sid = st["session_id"]
    extra: dict = {}
    for f, pids in ui.prep_fields(st).items():
        for pid in pids:
            extra.setdefault(pid, {})[f] = {"nominee_name": "Lakshmi", "nominee_relation": "wife",
                                            "pension_amount": 2000}[f]
    out = client.post(f"/sessions/{sid}/act", json={"extra_fields": extra}).json()
    assert not [o for o in out["outcomes"] if o.get("status") == "NEEDS_INFO"]
    forms = {i["product_id"]: i["payload"] for i in out["state"]["queue_items"] if i["product_id"]}
    assert {forms[p]["nominee_name"] for p in ("PMJJBY", "PMSBY", "APY")} == {"Lakshmi"}


# --- dates in tables ----------------------------------------------------------------------------

def test_event_datetime_is_short_and_local():
    assert re.fullmatch(r"\d{2} [A-Z][a-z]{2}, \d{2}:\d{2}", ui.event_datetime("2026-10-24T11:18:17+00:00"))
    assert ui.event_datetime(None) == "—" and ui.event_datetime("not a date") == "not a date"


def test_timeline_names_the_officer_and_reads_facts_as_text(client):
    st = client.post("/sessions", json={"customer_id": "C001", "officer_id": "officer-1"}).json()
    sid = st["session_id"]
    client.post(f"/sessions/{sid}/facts", json={"facts": {"grows_notified_crop": True, "in_notified_area": False},
                                               "question_id": "Q_NOTIFIED_CROP"})
    events = client.get(f"/sessions/{sid}/timeline").json()["events"]
    start = next(e for e in events if e["title"] == "Session started")
    assert start["summary"] == "Customer C001, by Banking Correspondent"
    facts = next(e for e in events if e.get("tool") == "update_profile")
    assert "Grows notified crop: yes" in facts["summary"] and "In notified area: no" in facts["summary"]
    assert "= true" not in facts["summary"]


# --- canvas, contrast and micro-motion (#24 follow-up) ------------------------------------------

def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = (int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b_)]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_tokens_pass_aa_on_every_surface_they_sit_on():
    c = ui.COLORS
    for bg in (c["bg"], c["surface"]):
        assert _contrast(c["heading"], bg) >= 7 and _contrast(c["ink"], bg) >= 7
        assert _contrast(c["muted"], bg) >= 4.5, bg
    # the muted grey fails AA on the active step's tint, so the CSS switches text there to the body colour
    assert _contrast(c["muted"], c["active_bg"]) < 4.5 <= _contrast(c["ink"], c["active_bg"])
    assert ".ys-stage-current :is(.ys-stage-name, .ys-stage-note) {{ color: var(--ys-ink); }}" in \
        (ROOT / "ui" / "components.py").read_text(encoding="utf-8")
    assert _contrast(c["primary"], c["active_bg"]) >= 4.5  # the "Now" text on the tint


def test_motion_is_minimal_and_off_under_reduced_motion():
    css = ui._CSS
    kill = "@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }"
    assert kill in css
    # only the live dot (and the running-tool chip from #18) loop; everything else plays once
    looping = set(re.findall(r"animation: (ys-[a-z-]+)[^;]*infinite", css))
    assert looping == {"ys-breathe", "ys-blink"}
    for name in ("ys-breathe", "ys-reveal", "ys-pill-in", "ys-disclose", "ys-bar-fill"):
        assert f"@keyframes {name}" in css
    assert "pointer-events: none" in css.split(".stApp::before, .stApp::after", 1)[1].split("}", 1)[0]


def test_live_dot_only_when_something_is_live():
    assert 'class="ys-live-dot"' in ui.mode_pill(True, "anthropic")
    assert "ys-live-dot" not in ui.mode_pill(False)


def test_only_real_blockers_get_the_warm_entry(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.customer_bar({"name": "R", "has_savings_account": True, "account_no_masked": "XXXXXX4521",
                     "aadhaar_seeded": False, "ekyc_done": None})
    html = out[0]
    assert html.count("ys-fact-blocker") == 1
    assert "ys-fact-blocker\" style" in html.split("Aadhaar not seeded")[0][-140:]  # on the ✕ chip, not the others


def test_product_cards_reveal_in_order_35ms_apart(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    monkeypatch.setattr(ui, "product_card", lambda *a, **k: None)
    ui.product_grid([{"product_id": p} for p in ("PMJDY", "PMJJBY", "PMSBY", "<x>")])
    style = out[0]
    assert ".st-key-ys-card-PMJDY{animation-delay:0ms}" in style
    assert ".st-key-ys-card-PMJJBY{animation-delay:35ms}" in style
    assert ".st-key-ys-card-PMSBY{animation-delay:70ms}" in style
    assert "<x>" not in style  # ids are sanitised before they reach the stylesheet
