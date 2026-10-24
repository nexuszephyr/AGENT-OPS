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
    st = client.post(f"/queue/{seeding['item_id']}/approve", json={}).json()["state"]
    steps = ui.plan_steps(st, ACTIONS)
    assert steps[0].title == ACTIONS["AADHAAR_SEEDING_REQUEST"] and steps[0].state == "done"
    assert sum(s.state == "done" for s in steps) == 1  # the approved step is not listed twice

    for i in st["queue_items"]:
        if i["status"] == "PENDING_APPROVAL":
            st = client.post(f"/queue/{i['item_id']}/approve", json={}).json()["state"]
    steps = ui.plan_steps(st, ACTIONS)
    assert steps and all(s.state in ("done", "todo") for s in steps)
    assert sum(s.state == "done" for s in steps) == 6


def test_rejected_item_shows_as_rejected(client):
    sid, _ = _ramesh_ready(client)
    st = client.post(f"/sessions/{sid}/act", json={"extra_fields": {"PMJJBY": NOMINEE}}).json()["state"]
    pmjjby = next(i for i in st["queue_items"] if i["product_id"] == "PMJJBY")
    client.post(f"/queue/{pmjjby['item_id']}/reject", json={"reason": "nominee typo"})
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
