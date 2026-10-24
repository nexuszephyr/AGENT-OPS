"""Officer console (issue #17): the plan stepper follows the real queue, and new components escape text."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

import components as ui  # noqa: E402

ACTIONS = {k: v["label"] for k, v in
           json.loads((ROOT / "app" / "rules" / "products.json").read_text(encoding="utf-8"))["actions"].items()}
NOMINEE = {"nominee_name": "Anjali", "nominee_relation": "daughter"}


@pytest.fixture
def client(conn):
    main.set_connection(conn)
    yield TestClient(main.app)
    main.set_connection(None)


def _ramesh_ready(client) -> tuple[str, dict]:
    st = client.post("/sessions", json={"customer_id": "C001"}).json()
    sid = st["session_id"]
    client.post(f"/sessions/{sid}/facts", json={"facts": {"grows_notified_crop": True, "in_notified_area": True},
                                               "question_id": "Q_NOTIFIED_CROP"})
    return sid, client.get(f"/sessions/{sid}/state").json()


def test_steps_before_anything_is_queued(client):
    _, st = _ramesh_ready(client)
    steps = ui.plan_steps(st, ACTIONS)
    assert steps[0].title == ACTIONS["AADHAAR_SEEDING_REQUEST"] and steps[0].state == "todo"
    assert "Unlocks PM-KISAN DBT readiness" in steps[0].detail
    blocked = [s for s in steps if s.state == "blocked"]
    assert [s.title for s in blocked] == ["Enrol in PM-KISAN DBT readiness"]
    assert ACTIONS["EKYC_REQUEST"] in blocked[0].detail
    assert not any(s.state == "done" for s in steps)


def test_steps_tick_as_items_are_approved(client):
    sid, _ = _ramesh_ready(client)
    extra = {p: NOMINEE for p in ("PMJJBY", "PMSBY")}
    extra["APY"] = {**NOMINEE, "pension_amount": 2000}
    extra["PMFBY"] = {"crop_name": "Ragi", "season": "Kharif 2026"}
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": extra}).json()["state"]
    assert {s.state for s in ui.plan_steps(st, ACTIONS) if s.state != "blocked"} == {"pending"}

    seeding = next(i for i in st["queue_items"] if i["action_id"] == "AADHAAR_SEEDING_REQUEST")
    st = client.post(f"/queue/{seeding['item_id']}/approve", json={"officer_id": "manager-1"}).json()["state"]
    steps = ui.plan_steps(st, ACTIONS)
    assert steps[0].title == ACTIONS["AADHAAR_SEEDING_REQUEST"] and steps[0].state == "done"
    assert sum(s.state == "done" for s in steps) == 1  # the approved step is not listed twice

    for i in st["queue_items"]:
        if i["status"] == "PENDING_APPROVAL":
            st = client.post(f"/queue/{i['item_id']}/approve", json={"officer_id": "manager-1"}).json()["state"]
    steps = ui.plan_steps(st, ACTIONS)
    assert steps and all(s.state in ("done", "todo") for s in steps)
    assert sum(s.state == "done" for s in steps) == 6


def test_rejected_item_shows_as_rejected(client):
    sid, _ = _ramesh_ready(client)
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {"PMJJBY": NOMINEE}}).json()["state"]
    pmjjby = next(i for i in st["queue_items"] if i["product_id"] == "PMJJBY")
    client.post(f"/queue/{pmjjby['item_id']}/reject", json={"officer_id": "manager-1", "reason": "nominee typo"})
    st = client.get(f"/sessions/{sid}/state").json()
    assert next(s for s in ui.plan_steps(st, ACTIONS) if s.title.endswith("Jeevan Jyoti Bima Yojana")).state \
        == "rejected"


def test_every_step_state_has_a_text_label():
    for key, (_colour, text) in ui.STEP_STATES.items():
        assert text.strip(), key
    assert set(ui.ITEM_STEP_STATE.values()) <= set(ui.STEP_STATES)


def test_new_components_escape_untrusted_text(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    evil = "<script>alert(1)</script>"
    ui.stepper([ui.Step(evil, "todo", evil), ui.Step("x", evil)])
    ui.customer_bar({"name": evil, "age": evil, "occupation": evil, "district": evil, "preferred_language": evil,
                     "has_savings_account": True, "account_no_masked": evil})
    ui.customer_bubble(evil, evil)
    ui.question_prompt(evil, evil)
    out.append(ui.language_chip(evil))
    assert "<script>" not in "".join(out)


def test_changed_card_keeps_its_identity(monkeypatch):
    """PR #40 review: a status change must not remount the card, or an open 'Why this status' collapses.
    Same container key and the same number of elements whether or not the card shows 'Changed from'."""
    keys: list[str | None] = []
    real_container = ui.st.container

    def spy(*args, **kwargs):
        keys.append(kwargs.get("key"))
        return real_container(*args, **kwargs)

    html: list[str] = []
    monkeypatch.setattr(ui.st, "container", spy)
    monkeypatch.setattr(ui, "_html", html.append)
    result = {"product_id": "PMFBY", "name": "PM Fasal Bima Yojana", "status": "ELIGIBLE", "benefit": "b",
              "failed_rules": [], "blocked_by": [], "unknown_fields": [], "source_url": "https://pmfby.gov.in"}
    ui.product_card(result, previous_status="UNKNOWN")
    changed_keys, changed_html = keys[:], len(html)
    keys.clear()
    html.clear()
    ui.product_card(result, previous_status=None)
    assert changed_keys == keys == ["ys-card-PMFBY"]
    assert changed_html == len(html)


def test_changed_card_says_what_it_was(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    result = {"product_id": "PMFBY", "name": "PM Fasal Bima Yojana", "status": "ELIGIBLE", "benefit": "",
              "failed_rules": [], "blocked_by": [], "unknown_fields": [], "source_url": "https://pmfby.gov.in"}
    ui.product_card(result, previous_status="UNKNOWN")
    assert any("Changed from Unknown to Eligible" in h for h in out)
    out.clear()
    ui.product_card(result, previous_status="ELIGIBLE")
    assert not any("Changed from" in h for h in out)


# --- agent pipeline + activity feed (issue #18) ----------------------------------------

def test_pipeline_marks_stages_and_the_active_tool(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    stages = [{"id": "ingest", "label": "Ingest", "name": "Data ingestion", "status": "done", "tools": {}},
              {"id": "decide", "label": "Decide", "name": "Intelligent decision-making", "status": "done",
               "tools": {"check_eligibility": 2}},
              {"id": "act", "label": "Act", "name": "Action execution", "status": "current",
               "tools": {"raise_request": 1, "fill_form": 3}},
              {"id": "report", "label": "Report", "name": "Outcome reporting", "status": "upcoming", "tools": {}}]
    ui.pipeline(stages, {"act": "2 prepared in this turn"}, active_tool="fill_form")
    html = out[0]
    assert html.count("ys-stage-done") == 2 and "ys-stage-current" in html and "ys-stage-upcoming" in html
    assert "✓ Done" in html and "● Now" in html and "○ Next" in html            # icon + text, not colour alone
    assert "check_eligibility ×2" in html and 'ys-chip ys-chip-active" title="Used last">fill_form ×3' in html
    assert "raise_request</span>" in html and "2 prepared in this turn" in html


def test_event_labels_carry_time_title_and_summary():
    label, icon = ui.event_label({"ts": None, "tone": "guardrail", "kind": "tool", "title": "fill_form refused",
                                  "summary": "PMJDY is NOT_ELIGIBLE"})
    assert icon == ":material/shield:" and "**Guardrail: fill\\_form refused**" in label
    _, icon = ui.event_label({"tone": "ok", "kind": "approve", "title": "Officer approved Q-1"})
    assert icon == ":material/task_alt:"
    long_label, _ = ui.event_label({"tone": "ok", "kind": "message", "title": "Officer typed", "summary": "x" * 300})
    assert long_label.endswith("…") and len(long_label) < 140


def test_event_labels_neutralise_markdown_from_typed_text():
    label, _ = ui.event_label({"tone": "ok", "kind": "message", "title": "Officer typed",
                               "summary": "[click](http://evil) **loud** <b>x</b>"})
    assert "\\[click\\](http://evil)" in label        # brackets escaped: no live link
    assert "\\*\\*loud\\*\\*" in label and "\\<b\\>" in label


def test_event_time_is_local_clock_time():
    assert len(ui.event_time("2026-10-24 07:26:38")) == 8   # HH:MM:SS in the UI machine's timezone
    assert ui.event_time(None) == "" and ui.event_time("not a time") == "not a time"


def test_pipeline_escapes_text(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    evil = "<script>alert(1)</script>"
    ui.pipeline([{"id": evil, "label": evil, "name": evil, "status": evil, "tools": {evil: 2}}], {evil: evil}, evil)
    assert "<script>" not in out[0]


# --- approval queue (issue #19) ----------------------------------------------------------------

def test_revision_diff_shows_before_and_after_and_escapes(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.revision_diff([{"field": "nominee_name", "before": "Anjeli", "after": "Anjali"},
                      {"field": "<b>x</b>", "before": None, "after": "<script>alert(1)</script>"}])
    html = out[0]
    assert "What changed since the rejection" in html and "At rejection" in html and "Now" in html
    assert 'class="ys-diff-before" aria-label="was">Anjeli' in html and 'class="ys-diff-after">Anjali' in html
    assert "Nominee name" in html and "—" in html            # a missing old value reads as a dash
    assert "<script>" not in html and "<b>x" not in html


def test_rejection_note_and_group_header_escape(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    evil = "<script>alert(1)</script>"
    ui.rejection_note(evil, evil)
    ui.queue_group_header(evil, evil, {"PENDING_APPROVAL": 2})
    assert "<script>" not in "".join(out)
    assert "Pending approval" in out[1] and '<span class="ys-count">2</span>' in out[1]


def test_count_badges_put_escalations_first_and_skip_zeros():
    html = ui.count_badges({"PENDING_APPROVAL": 3, "NEEDS_OFFICER": 1, "REJECTED": 0})
    assert html.index("Needs officer") < html.index("Pending approval") and "Rejected" not in html


def test_pdf_preview_renders_the_real_form(tmp_path):
    from app.forms import fill

    path = fill.render_pdf("Q-TEST01", "PMJJBY enrollment form",
                           {"name": "Priya Nair", "aadhaar_masked": "XXXX-XXXX-5520"}, out_dir=tmp_path)
    png, pages = ui.pdf_first_page_png(Path(path).read_bytes(), width_px=300)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and pages == 1
    import io

    from PIL import Image

    w, h = Image.open(io.BytesIO(png)).size
    assert w == 300 and 1.35 < h / w < 1.48   # an A4 page


# --- branch scan call list (issue #38) ---------------------------------------------------------

ROW = {"customer_id": "C106", "name": "Nethra Bhat", "district": "Tumakuru", "occupation": "farmer", "rank": 1,
       "urgent": True, "value_inr_total": 0,
       "opportunities": [{"product_id": "PMJJBY", "status": "ELIGIBLE"},
                         {"product_id": "PMKISAN_DBT", "status": "ELIGIBLE_BLOCKED"}],
       "blockers": [{"action_id": "AADHAAR_SEEDING_REQUEST", "unlocks": ["PMKISAN_DBT"]}],
       "reason": "Eligible for PMJJBY, PMKISAN_DBT · Aadhaar not seeded → PM-KISAN payments will fail"}


def test_inr_uses_indian_digit_grouping():
    assert [ui.inr(v) for v in (None, 0, 436, 12345, 123456, 12345678)] == \
        ["—", "₹0", "₹436", "₹12,345", "₹1,23,456", "₹1,23,45,678"]


def test_call_row_shows_rank_urgency_chips_and_reason(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.call_row(ROW, {"PMJJBY": "PM Jeevan Jyoti Bima Yojana"})
    html = out[0]
    assert 'aria-label="Rank 1"' in html and "PM-KISAN at risk" in html and "Nethra Bhat" in html
    assert "✓</span>PMJJBY" in html and "→</span>PMKISAN_DBT" in html          # icon + text per opportunity
    assert "needs a prerequisite first" in html                               # blocked chip explains itself
    assert "ys-call-value" not in html                                        # no ₹0 shown before #36
    assert "PM-KISAN payments will fail" in html


def test_call_row_shows_value_and_no_urgency_when_not_urgent(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.call_row({**ROW, "urgent": False, "value_inr_total": 123456})
    assert "₹1,23,456" in out[0] and "PM-KISAN at risk" not in out[0]


def test_call_row_escapes(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    evil = "<script>alert(1)</script>"
    ui.call_row({**ROW, "name": evil, "district": evil, "occupation": evil, "reason": evil, "customer_id": evil,
                 "opportunities": [{"product_id": evil, "status": "ELIGIBLE"}]}, {evil: evil})
    assert "<script>" not in out[0]


def test_filter_call_list_keeps_ranking_and_combines_filters():
    rows = [{**ROW, "customer_id": "A", "rank": 1},
            {**ROW, "customer_id": "B", "rank": 2, "urgent": False, "district": "Mandya", "blockers": [],
             "opportunities": [{"product_id": "SSY", "status": "ELIGIBLE"}]},
            {**ROW, "customer_id": "C", "rank": 3, "district": "Mandya"}]
    ids = lambda rs: [r["customer_id"] for r in rs]  # noqa: E731
    assert ids(ui.filter_call_list(rows)) == ["A", "B", "C"]
    assert ids(ui.filter_call_list(rows, urgent_only=True)) == ["A", "C"]
    assert ids(ui.filter_call_list(rows, products=["SSY"])) == ["B"]
    assert ids(ui.filter_call_list(rows, blockers=["AADHAAR_SEEDING_REQUEST"])) == ["A", "C"]
    assert ids(ui.filter_call_list(rows, districts=["Mandya"], urgent_only=True)) == ["C"]
    assert ids(ui.filter_call_list(rows, products=["APY"])) == []
