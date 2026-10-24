"""UI-driven walkthrough for scripts/smoke.py (#26): Streamlit AppTest, in-process, against the live API.

Every step is one PASS/FAIL line: no exception in the app run, plus one concrete outcome. Decisions go only
through the UI's own buttons, as the app does: officer-1 (maker) prepares, manager-1 (checker) approves / rejects.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

APP = str(Path(__file__).resolve().parent.parent / "ui" / "streamlit_app.py")
MAKER, CHECKER = "officer-1", "manager-1"


# --- helpers -------------------------------------------------------------------------------
def fresh_app() -> Any:
    """A new AppTest session (its own st.session_state), first run done."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    st.cache_data.clear()  # shared by every AppTest in this process; HTTP writes from elsewhere don't clear it
    return AppTest.from_file(APP, default_timeout=60).run()


def act_as(at: Any, officer_id: str) -> Any:
    return at.selectbox(key="officer_id").set_value(officer_id).run()


def click(at: Any, key_or_label: str, form: str | None = None) -> Any:
    """Click a button by key, else by label (inside the form whose key contains `form`, if given)."""
    for b in at.button:
        if b.key == key_or_label or (b.label == key_or_label and (form is None or form in str(b.form_id))):
            return b.click().run()
    raise LookupError(f"no button {key_or_label!r}" + (f" in form {form!r}" if form else ""))


def step(sm: Any, name: str, at: Any, outcome: Callable[[], object], detail: Callable[[], object] = str) -> None:
    """One check: the app run raised nothing, and `outcome()` holds. Exceptions are printed loudly."""
    errors = [str(e.value) for e in at.exception]
    if errors:
        print(f"      !! app exception in step '{name}':\n        " + "\n        ".join(errors))
    try:
        ok, info = bool(outcome()), detail()
    except Exception as e:  # noqa: BLE001 - a broken outcome is a failed step, not a crashed smoke run
        ok, info = False, repr(e)
    sm.check(name, not errors and ok, errors or info)


def statuses(at: Any) -> dict[str, str]:
    return {r["product_id"]: r["status"] for r in at.session_state["state"]["results"]}


def caption_texts(at: Any) -> list[str]:
    return [c.value for c in at.caption]


# --- the walkthrough -------------------------------------------------------------------------
def run(sm: Any, http: Callable[..., tuple[int, Any]], api: str) -> None:
    def item(iid: str) -> dict:
        return next(i for i in http("GET", f"{api}/queue")[1] if i["item_id"] == iid)

    # 1-2 Ramesh, as the maker: the picker's Start session, then one recorded answer
    at = fresh_app()
    click(at, "start_C001")
    step(sm, "UI 1 Start session (Ramesh, maker): products render; PMFBY Unknown, PM-KISAN blocked", at,
         lambda: any(h.value == "Products" for h in at.subheader) and statuses(at)["PMFBY"] == "UNKNOWN"
         and statuses(at)["PMKISAN_DBT"] == "ELIGIBLE_BLOCKED", lambda: statuses(at))
    sid = at.session_state["state"]["session_id"]
    for field in ("grows_notified_crop", "in_notified_area"):  # officer-only crop fields
        at.radio(key=f"f_{sid}_{field}").set_value("Yes")
    click(at.run(), "record_answer")  # the button enables once a rerun sees an answer
    step(sm, "UI 2 Record answer (notified crop + area = Yes): PMFBY becomes Eligible", at,
         lambda: statuses(at)["PMFBY"] == "ELIGIBLE", lambda: statuses(at))

    # 3 Arjun: "Doesn't know" resolves the question with no answer, so its product needs a manual check
    ar = fresh_app()
    click(ar, "start_C004")
    question = ar.session_state["state"]["next_question"]["question"]
    click(ar, "dont_know")
    step(sm, "UI 3 Doesn't know (Arjun, income-tax status): APY -> Verify manually, no question left", ar,
         lambda: question["resolves"] == ["APY"] and statuses(ar)["APY"] == "VERIFY_MANUALLY"
         and ar.session_state["state"]["next_question"]["question"] is None,
         lambda: (statuses(ar)["APY"], ar.session_state["state"]["next_question"]))

    # 4 Prepare all steps (rules-only), 5 the maker can't approve, 6 the checker approves
    # the #24 form asks each shared detail once (no JSON box); fill whichever fields it shows
    prep = {"nominee_name": "Anjeli", "nominee_relation": "daughter",  # misspelt: rejected in step 7
            "pension_amount": "2000", "crop_name": "Ragi", "season": "Kharif 2026"}
    for field, value in prep.items():
        if any(w.key == f"prep_{sid}_{field}" for w in at.text_input):
            at.text_input(key=f"prep_{sid}_{field}").set_value(value)
    click(at, "prepare_all")
    st = at.session_state["state"]
    items = st["queue_items"]
    step(sm, "UI 4 Prepare all steps: 6 items pending approval; the plan starts with Aadhaar seeding", at,
         lambda: len(items) == 6 and all(i["status"] == "PENDING_APPROVAL" for i in items)
         and st["plan"]["steps"][0].get("action_id") == "AADHAAR_SEEDING_REQUEST",
         lambda: [(i["action_id"] or i["product_id"], i["status"]) for i in items])
    keys = {f"ok_{i['item_id']}" for i in items}
    step(sm, "UI 5 maker-checker: Approve is disabled for officer-1 on items they prepared", at,
         lambda: {b.key for b in at.button if b.key in keys and b.disabled} == keys,
         lambda: [(b.key, b.disabled) for b in at.button if b.key in keys])
    act_as(at, CHECKER)
    form = next(i for i in items if i["product_id"] == "PMJJBY")
    for i in [*(i for i in items if i["action_id"]), form]:  # seeding, eKYC, then one form
        click(at, f"ok_{i['item_id']}")
    decided = [item(i["item_id"])["status"] for i in items if i["action_id"] or i is form]
    step(sm, "UI 6 manager-1 approves seeding, eKYC and the PMJJBY form: submitted; PM-KISAN unblocks", at,
         lambda: decided == ["SUBMITTED"] * 3 and statuses(at)["PMKISAN_DBT"] == "ELIGIBLE",
         lambda: (decided, statuses(at)["PMKISAN_DBT"]))

    # 7 reject with a reason (checker) -> the maker edits the nominee and sends it back
    rid = next(i["item_id"] for i in items if i["product_id"] == "PMSBY")
    at.text_input(key=f"why_{rid}").set_value("nominee name misspelt")
    click(at, "Reject with this reason", form=f"rej_{rid}")
    rejected = item(rid)["status"]
    act_as(at, MAKER)
    at.selectbox(key=f"rf_{rid}").set_value("nominee_name").run()
    at.text_input(key=f"rv_{rid}_nominee_name").set_value("Anjali")
    click(at, "Save and send back", form=f"edit_{rid}")
    revised = item(rid)
    step(sm, "UI 7 reject ('nominee name misspelt') -> maker fixes the nominee -> pending again, diff renders", at,
         lambda: rejected == "REJECTED" and revised["status"] == "PENDING_APPROVAL"
         and revised["revision_count"] == 1 and any("ys-diff" in m.value for m in at.markdown),
         lambda: (rejected, revised["status"], revised["revision_count"]))

    # 8 Branch scan tab: Run scan draws the ranked call list
    click(at, "run_scan")
    step(sm, "UI 8 Run scan: the call list renders with a Start session per row", at,
         lambda: sum(1 for b in at.button if (b.key or "").startswith("scan_start_")) > 0,
         lambda: [b.key for b in at.button if (b.key or "").startswith("scan_start_")][:3])

    dashboard(sm, http, api, sid)


def dashboard(sm: Any, http: Callable[..., tuple[int, Any]], api: str, sid: str) -> None:
    """9 Dashboard tab after the flows: tiles, charts, filters (product, detail) and the audit filters."""
    from streamlit.elements.vega_charts import VegaLiteState

    from ui.components import filter_results, panel_counts

    d = fresh_app()
    heads = {h.value for h in d.subheader}
    step(sm, "UI 9 dashboard: KPI tiles, funnel, Eligibility by product and Audit log render", d,
         lambda: {"Enrolment funnel", "Eligibility by product", "Audit log"} <= heads
         and any("Customers screened" in m.value for m in d.markdown), lambda: sorted(heads))

    # What a click on the PMJJBY bar stores: the chart's selection state (AppTest can't click Vega marks)
    d.session_state["dash_prod_0"] = VegaLiteState({"selection": {"product": [{"product_id": "PMJJBY"}]}})
    d.run()
    table = next((t.value for t in d.dataframe if "Product" in t.value.columns), None)
    step(sm, "UI 9 dashboard filtered to PMJJBY: chip shows, the table holds PMJJBY rows only", d,
         lambda: d.session_state["dash_product"] == "PMJJBY"
         and any("Filtered:** Product PMJJBY" in m.value for m in d.markdown)
         and table is not None and len(table) > 0 and set(table["Product"]) == {"PMJJBY"},
         lambda: None if table is None else sorted(set(table["Product"])))

    rows = filter_results(http("GET", f"{api}/dashboard")[1]["customer_results"], "PMJJBY")
    docs = panel_counts(rows, "doc", {})
    if docs:  # a missing-document bar within PMJJBY, then a rule that isn't in this view (#50 review fix)
        d.session_state["dash_detail"] = ("doc", docs[0]["id"])
        d.run()
        table = next((t.value for t in d.dataframe if "Product" in t.value.columns), None)
        step(sm, f"UI 9 dashboard detail filter (missing {docs[0]['id']}): kept, the table renders", d,
             lambda: d.session_state["dash_detail"] == ("doc", docs[0]["id"]) and table is not None and len(table) > 0,
             lambda: d.session_state["dash_detail"])
    d.session_state["dash_detail"] = ("rule", "R-APY-AGE-MAX")
    d.run()
    step(sm, "UI 9 dashboard drops a rule filter with no bar under the product filter", d,
         lambda: "dash_detail" not in d.session_state, lambda: "dash_detail" in d.session_state)

    facets = http("GET", f"{api}/audit/facets")[1]
    filters = [("session", d.selectbox, "audit_session", sid, "All"),
               ("actor", d.selectbox, "audit_actor", "officer", "All"),
               ("event", d.multiselect, "audit_events", ["reject"], []),
               ("search", d.text_input, "audit_q", "misspelt", "")]
    for name, widget, key, value, reset in filters:
        widget(key=key).set_value(value).run()
        shown = [c for c in caption_texts(d) if c.startswith("Showing") and "(filtered)" in c]
        step(sm, f"UI 9 audit filter by {name} ({value}): filtered rows render", d,
             lambda shown=shown: bool(shown) and not shown[0].startswith("Showing 0 "), lambda shown=shown: shown)
        widget(key=key).set_value(reset).run()
    sm.check("UI 9 audit facets list the UI session and the reject event",
             sid in facets["sessions"] and "reject" in facets["events"], facets)
