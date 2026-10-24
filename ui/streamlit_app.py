"""Yojana Saathi officer UI.

    streamlit run ui/streamlit_app.py     (API must be running on API_URL, default :8000)

Tabs: Officer console | Approval queue | Branch dashboard.
Look and feel: .streamlit/config.toml (theme) + ui/components.py (shared components, issue #16).
"""
from __future__ import annotations

import json
import os

import requests
import streamlit as st
from components import (
    COLORS,
    app_header,
    card_title,
    empty_state,
    key_values,
    kpi_tiles,
    product_card,
    queue_badge,
    section_header,
    setup_page,
    status_legend,
)

API = os.getenv("API_URL", "http://localhost:8000")
OFFICER = os.getenv("YS_OFFICER_ID", "officer-1")
OFFICER_NAME = os.getenv("YS_OFFICER_NAME", "Officer 1")
BRANCH = os.getenv("YS_BRANCH", "Mandya (demo branch)")

QUESTION_FACT_INPUTS = {
    # question_id -> list of (field, widget kind)
    "Q_ITP": [("is_income_tax_payer", "bool")],
    "Q_NOTIFIED_CROP": [("grows_notified_crop", "bool"), ("in_notified_area", "bool")],
    "Q_LAND": [("is_landholder", "bool")],
    "Q_GIRLS": [("girl_children_under_10", "int")],
    "Q_SSY_EXISTING": [("existing_ssy_accounts", "int")],
    "Q_PENSION": [("monthly_pension", "int")],
    "Q_GOVT": [("is_govt_employee", "bool")],
    "Q_PROF": [("is_registered_professional", "bool")],
    "Q_SEEDED": [("aadhaar_seeded", "bool")],
    "Q_EKYC": [("ekyc_done", "bool")],
    "Q_HAS_ACCOUNT": [("has_savings_account", "bool")],
    "Q_AGE": [("age", "int")],
}
# Identity fields come from the bank record: hidden from the officer's edit picker.
LOCKED_FIELDS = {"customer_id", "form", "prepared_on", "aadhaar_masked", "account_no_masked", "mobile_masked"}

setup_page()


def api(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{API}{path}", timeout=120, **kw)
    except requests.ConnectionError:
        st.error(f"Can't reach the API at {API}. Start it with `uvicorn app.api.main:app`, then reload this page.")
        st.stop()
    if r.status_code >= 400:
        st.error(f"The API returned {r.status_code}: {r.text}")
        return None
    return r.json() if "json" in r.headers.get("content-type", "") else r.content


def set_state(new_state: dict | None) -> None:
    if new_state:
        st.session_state["state"] = new_state


def label(field: str) -> str:
    return field.replace("_", " ").capitalize()


# --- header -----------------------------------------------------------------------
health = api("GET", "/health") or {}
app_header(llm_configured=bool(health.get("llm_configured")), provider=health.get("provider"),
           officer=OFFICER_NAME, branch=BRANCH)

tab_console, tab_queue, tab_dash = st.tabs(["Officer console", "Approval queue", "Branch dashboard"])

# --- Officer console ------------------------------------------------------------------
with tab_console:
    customers = api("GET", "/customers") or []
    labels = {f"{c['customer_id']} · {c['name']} ({c['age']}, {c['occupation']}, {c['district']})": c["customer_id"]
              for c in customers}
    col_pick, col_btn = st.columns([4, 1], vertical_alignment="bottom")
    choice = col_pick.selectbox("Customer", list(labels))
    if col_btn.button("Start session", type="primary", width="stretch", disabled=not labels):
        set_state(api("POST", "/sessions", json={"customer_id": labels[choice], "officer_id": OFFICER}))
        st.session_state["last_reply"] = None

    state = st.session_state.get("state")
    if not state:
        empty_state("No session yet", "Pick a customer and select Start session to screen them for every scheme.")
    else:
        state = api("GET", f"/sessions/{state['session_id']}/state") or state
        set_state(state)
        left, right = st.columns([3, 2], gap="large")

        with left:
            section_header(f"Session {state['session_id']}", f"Stage: {label(state['state'])}")
            for m in state["messages"][-10:]:
                if m["role"] == "user":
                    if not m["content"].startswith("The officer rejected"):
                        st.chat_message("user").write(m["content"])
                    continue
                try:
                    rep = json.loads(m["content"])
                except json.JSONDecodeError:
                    rep = {"to_officer": m["content"]}
                with st.chat_message("assistant"):
                    st.write(rep.get("to_officer", ""))
                    if rep.get("to_customer"):
                        st.info(f"To customer ({rep['to_customer']['lang']}): {rep['to_customer']['text']}")
                    if rep.get("question"):
                        st.markdown(f"**Ask:** {rep['question']['text']}")

            text = st.chat_input("Type what the customer said, or an instruction")
            if text:
                out = api("POST", f"/sessions/{state['session_id']}/message", json={"text": text})
                if out:
                    set_state(out["state"])
                    st.rerun()

            # Officer fact entry: works with or without the LLM
            nq = state["next_question"]["question"]
            if nq:
                with st.expander(f"Answer: {nq['text_en']}", expanded=not health.get("llm_configured")):
                    if nq.get("officer_note"):
                        st.caption(nq["officer_note"])
                    # Nothing is preselected: a field the officer doesn't set stays unknown (rule 4).
                    facts = {}
                    for field, kind in QUESTION_FACT_INPUTS.get(nq["question_id"], []):
                        if kind == "bool":
                            ans = st.radio(label(field), ["Yes", "No"], index=None, horizontal=True,
                                           key=f"f_{state['session_id']}_{field}")
                            if ans is not None:
                                facts[field] = ans == "Yes"
                        else:
                            num = st.number_input(label(field), min_value=0, step=1, value=None,
                                                  key=f"f_{state['session_id']}_{field}")
                            if num is not None:
                                facts[field] = int(num)
                    b1, b2 = st.columns(2)
                    if b1.button("Record answer", type="primary", disabled=not facts, width="stretch"):
                        out = api("POST", f"/sessions/{state['session_id']}/facts",
                                  json={"facts": facts, "question_id": nq["question_id"]})
                        if out:
                            rej = out["result"].get("rejected_fields")
                            if rej:
                                st.warning("Not recorded: " + "; ".join(f"{r['field']} ({r['reason']})" for r in rej))
                            set_state(out["state"])
                            st.rerun()
                    # Resolves the question with no answer: its products go to VERIFY_MANUALLY.
                    if b2.button("Customer doesn't know", width="stretch"):
                        out = api("POST", f"/sessions/{state['session_id']}/facts",
                                  json={"facts": {}, "question_id": nq["question_id"]})
                        if out:
                            set_state(out["state"])
                            st.rerun()
            elif state["next_question"].get("reason"):
                st.caption(f"No more questions: {state['next_question']['reason']}.")

            with st.expander("Prepare all steps (rules-only)", expanded=False):
                st.caption("Nominee and other details per product, as JSON. For example: "
                           '{"PMJJBY": {"nominee_name": "Anjali", "nominee_relation": "daughter"}}')
                extra_txt = st.text_area("Extra fields", value="{}", height=100)
                if st.button("Prepare all steps"):
                    try:
                        extra = json.loads(extra_txt or "{}")
                    except json.JSONDecodeError:
                        st.error("Extra fields must be valid JSON, for example {}.")
                        extra = None
                    if extra is not None:
                        out = api("POST", f"/sessions/{state['session_id']}/act", json={"extra_fields": extra})
                        if out:
                            for o in out["outcomes"]:
                                if o.get("error"):
                                    st.warning(f"Step {o.get('step')}: {o['error']}")
                                elif o.get("status") == "NEEDS_INFO":
                                    st.warning(f"Step {o.get('step')} ({o.get('product_id')}) needs: "
                                               + ", ".join(o.get("missing_mandatory", [])))
                            set_state(out["state"])
                            st.rerun()

        with right:
            section_header("Products", "Decided by the rules engine; open a card to see the cited rules.")
            status_legend()
            for r in state["results"]:
                product_card(r)

            section_header("Plan", "In the order the steps must happen.")
            steps = state["plan"]["steps"]
            if not steps:
                empty_state("Nothing to prepare yet", "Steps appear once a product is eligible.")
            for s in steps:
                target = s.get("action_id") if s["type"] == "SERVICE_REQUEST" else s["product_id"]
                after = f" (after {', '.join(s['after'])})" if s.get("after") else ""
                unlocks = f": unlocks {', '.join(s['unlocks'])}" if s.get("unlocks") else ""
                st.markdown(f"{s['order']}. **{target}**{unlocks}{after}")
            if state["plan"]["checklist"]:
                section_header("Customer checklist", "Documents to bring, once each.")
                for c in state["plan"]["checklist"]:
                    st.markdown(f"- {c['label']} *(for {', '.join(c['used_by'])})*")

# --- Approval queue ----------------------------------------------------------------------
with tab_queue:
    items = api("GET", "/queue") or []
    pending = [i for i in items if i["status"] in ("PENDING_APPROVAL", "REJECTED", "NEEDS_OFFICER")]
    section_header("Waiting for you", "Every form and service request needs your approval before it is submitted.")
    if not pending:
        empty_state("Nothing waiting for approval",
                    "Prepared forms and service requests appear here. Start a session in the Officer console.")
    for i in pending:
        target = i["product_id"] or i["action_id"]
        with st.container(border=True):
            card_title(target, f"{i['item_id']} · {label(i['kind'])} · customer {i['customer_id']}"
                               f" · revisions {i['revision_count']}", queue_badge(i["status"]))
            if i.get("reject_reason"):
                st.caption(f"Last rejection: {i['reject_reason']}")
            with st.expander("Form fields"):
                key_values(i["payload"])
            c1, c2, c3 = st.columns([1, 1, 3], vertical_alignment="bottom")
            if i["pdf_path"]:
                pdf = api("GET", f"/queue/{i['item_id']}/pdf")
                if pdf:
                    c1.download_button("Download PDF", pdf, file_name=f"{i['item_id']}.pdf",
                                       key=f"pdf_{i['item_id']}", width="stretch")
            if i["status"] == "PENDING_APPROVAL":
                if c2.button("Approve", key=f"ok_{i['item_id']}", type="primary", width="stretch"):
                    api("POST", f"/queue/{i['item_id']}/approve", json={"officer_id": OFFICER})
                    st.rerun()
                reason = c3.text_input("Reason for rejecting", key=f"why_{i['item_id']}",
                                       placeholder="e.g. Nominee name is Anjali, not Anjeli")
                if c3.button("Reject", key=f"no_{i['item_id']}", disabled=not reason):
                    out = api("POST", f"/queue/{i['item_id']}/reject", json={"officer_id": OFFICER, "reason": reason})
                    if out:
                        st.info(out["reply"]["to_officer"])
                    st.rerun()
            elif i["status"] in ("REJECTED", "NEEDS_OFFICER"):
                # Officer fixes it directly (always available; the only path in rules-only mode)
                with st.expander("Edit and send back for approval",
                                 expanded=i["status"] == "NEEDS_OFFICER" or not health.get("llm_configured")):
                    editable = [k for k in i["payload"] if k not in LOCKED_FIELDS] or list(i["payload"])
                    field = st.selectbox("Field", editable, key=f"rf_{i['item_id']}", format_func=label)
                    value = st.text_input("New value", value=str(i["payload"].get(field) or ""),
                                          key=f"rv_{i['item_id']}_{field}")
                    if st.button("Save and send back", key=f"rs_{i['item_id']}", type="primary"):
                        out = api("POST", f"/queue/{i['item_id']}/revise",
                                  json={"officer_id": OFFICER, "changes": {field: value},
                                        "explanation": f"officer corrected {field}"})
                        if out:
                            st.rerun()

    done = [i for i in items if i["status"] == "SUBMITTED"]
    if done:
        section_header("Approved and submitted", "Submission is mocked in this prototype.")
        st.dataframe([{"Item": i["item_id"], "Customer": i["customer_id"], "Kind": label(i["kind"]),
                       "Product / action": i["product_id"] or i["action_id"], "Approved by": i["decided_by"],
                       "Approved at": i["decided_at"]} for i in done], width="stretch", hide_index=True)

# --- Dashboard ------------------------------------------------------------------------------
with tab_dash:
    d = api("GET", "/dashboard") or {}
    if d:
        section_header("Branch outcomes", "Across the latest session for each customer.")
        kpi_tiles([
            ("Customers screened", d["customers_screened"]),
            ("Products eligible", d["products_eligible"]),
            ("Pending approval", d["items_pending"]),
            ("Approved", d["items_approved"]),
            ("Rejected", d["items_rejected"], d["items_rejected"] > 0),
            ("Escalated to officer", d["items_escalated"], d["items_escalated"] > 0),
        ])
        if d.get("sessions_by_state"):
            st.caption("Sessions by stage: " + ", ".join(f"{label(s)} {n}" for s, n in d["sessions_by_state"].items()))
        cols = st.columns(3, gap="medium")
        panels = [("Blocked by a prerequisite", "blocked_by_action", "No customer is waiting on a prerequisite."),
                  ("Most common not-eligible rules", "not_eligible_reasons", "No rule has excluded anyone yet."),
                  ("Most common missing documents", "top_missing_docs", "No documents are missing.")]
        for col, (title, key, none_text) in zip(cols, panels, strict=True):
            with col:
                st.markdown(f"**{title}**")
                if d[key]:
                    # one readable row per bar instead of squeezing every rule into a fixed height
                    st.bar_chart(d[key], horizontal=True, height=max(160, 34 * len(d[key])),
                                 color=COLORS["primary"])
                else:
                    empty_state(none_text)
    section_header("Audit log", "Every agent, officer and system action, newest first.")
    rows = api("GET", "/audit", params={"limit": 200}) or []
    if not rows:
        empty_state("No activity yet", "Actions are recorded here as soon as a session starts.")
    else:
        st.dataframe([{"Time": r["ts"], "Session": r["session_id"], "Actor": r["actor"], "Event": r["event"],
                       "Details": json.dumps(r["payload"], ensure_ascii=False)[:200]} for r in rows],
                     width="stretch", height=400, hide_index=True)
