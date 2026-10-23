"""Yojana Saathi officer UI.

    streamlit run ui/streamlit_app.py     (API must be running on API_URL, default :8000)

Tabs: Officer console | Approval queue | Branch dashboard.
"""
from __future__ import annotations

import json
import os

import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
OFFICER = "officer-1"

STATUS_STYLE = {
    "ELIGIBLE": ("🟤", "Eligible"),
    "ELIGIBLE_BLOCKED": ("🟠", "Eligible – blocked"),
    "NOT_ELIGIBLE": ("⚪", "Not eligible"),
    "UNKNOWN": ("❔", "Unknown"),
    "ALREADY_ENROLLED": ("✔️", "Already enrolled"),
    "VERIFY_MANUALLY": ("⚠️", "Verify manually"),
}
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

st.set_page_config(page_title="Yojana Saathi", page_icon="🏦", layout="wide")


def api(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{API}{path}", timeout=120, **kw)
    except requests.ConnectionError:
        st.error(f"Cannot reach the API at {API}. Start it with `uvicorn app.api.main:app`.")
        st.stop()
    if r.status_code >= 400:
        st.error(f"{r.status_code}: {r.text}")
        return None
    return r.json() if "json" in r.headers.get("content-type", "") else r.content


def set_state(new_state: dict | None) -> None:
    if new_state:
        st.session_state["state"] = new_state


# --- header -----------------------------------------------------------------------
health = api("GET", "/health") or {}
st.title("🏦 Yojana Saathi")
st.caption("Financial-inclusion onboarding agent · every action needs officer approval · synthetic data")
if not health.get("llm_configured"):
    st.warning("LLM not configured: running in **rules-only mode**. Use the fact form and 'Prepare all' below.")

tab_console, tab_queue, tab_dash = st.tabs(["Officer console", "Approval queue", "Branch dashboard"])

# --- Officer console ------------------------------------------------------------------
with tab_console:
    customers = api("GET", "/customers") or []
    labels = {f"{c['customer_id']} · {c['name']} ({c['age']}, {c['occupation']}, {c['district']})": c["customer_id"]
              for c in customers}
    col_pick, col_btn = st.columns([4, 1])
    choice = col_pick.selectbox("Customer", list(labels))
    if col_btn.button("Start session", use_container_width=True):
        set_state(api("POST", "/sessions", json={"customer_id": labels[choice], "officer_id": OFFICER}))
        st.session_state["last_reply"] = None

    state = st.session_state.get("state")
    if state:
        state = api("GET", f"/sessions/{state['session_id']}/state") or state
        set_state(state)
        left, right = st.columns([3, 2])

        with left:
            st.subheader(f"Session {state['session_id']} · {state['state']}")
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

            text = st.chat_input("Type what the customer said, or an instruction…")
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
                            ans = st.radio(field, ["Yes", "No"], index=None, horizontal=True,
                                           key=f"f_{state['session_id']}_{field}")
                            if ans is not None:
                                facts[field] = ans == "Yes"
                        else:
                            num = st.number_input(field, min_value=0, step=1, value=None,
                                                  key=f"f_{state['session_id']}_{field}")
                            if num is not None:
                                facts[field] = int(num)
                    b1, b2 = st.columns(2)
                    if b1.button("Record answer", disabled=not facts):
                        out = api("POST", f"/sessions/{state['session_id']}/facts",
                                  json={"facts": facts, "question_id": nq["question_id"]})
                        if out:
                            rej = out["result"].get("rejected_fields")
                            if rej:
                                st.warning(f"Not recorded: {rej}")
                            set_state(out["state"])
                            st.rerun()
                    # Resolves the question with no answer: its products go to VERIFY_MANUALLY.
                    if b2.button("Customer doesn't know"):
                        out = api("POST", f"/sessions/{state['session_id']}/facts",
                                  json={"facts": {}, "question_id": nq["question_id"]})
                        if out:
                            set_state(out["state"])
                            st.rerun()
            elif state["next_question"].get("reason"):
                st.caption(f"No more questions: {state['next_question']['reason']}")

            with st.expander("Prepare all actionable steps (rules-only)", expanded=False):
                st.caption("Nominee etc. per product as JSON, e.g. "
                           '{"PMJJBY": {"nominee_name": "Anjali", "nominee_relation": "daughter"}}')
                extra_txt = st.text_area("Extra fields", value="{}", height=100)
                if st.button("Prepare all"):
                    try:
                        extra = json.loads(extra_txt or "{}")
                    except json.JSONDecodeError:
                        st.error("Extra fields must be valid JSON")
                        extra = None
                    if extra is not None:
                        out = api("POST", f"/sessions/{state['session_id']}/act", json={"extra_fields": extra})
                        if out:
                            for o in out["outcomes"]:
                                if o.get("error") or o.get("status") == "NEEDS_INFO":
                                    st.warning(o)
                            set_state(out["state"])
                            st.rerun()

        with right:
            st.subheader("Products")
            for r in state["results"]:
                icon, label = STATUS_STYLE.get(r["status"], ("•", r["status"]))
                with st.expander(f"{icon} {r['product_id']}: {label}"):
                    st.caption(r["benefit"])
                    for f in r["failed_rules"]:
                        st.markdown(f"✗ {f['text']}  `{f['id']}`")
                    for a in r["blocked_by"]:
                        st.markdown(f"⛔ needs **{a}**")
                    for u in r["unknown_fields"]:
                        st.markdown(f"❔ missing `{u}`")
                    st.markdown(f"[Source]({r['source_url']}) · verified {r['last_verified']}")

            st.subheader("Plan")
            for s in state["plan"]["steps"]:
                target = s.get("action_id") if s["type"] == "SERVICE_REQUEST" else s["product_id"]
                extra = f" (after {', '.join(s['after'])})" if s.get("after") else ""
                unlocks = f" → unlocks {', '.join(s['unlocks'])}" if s.get("unlocks") else ""
                st.markdown(f"{s['order']}. **{target}**{unlocks}{extra}")
            if state["plan"]["checklist"]:
                st.subheader("Customer checklist")
                for c in state["plan"]["checklist"]:
                    st.markdown(f"- {c['label']} *(for {', '.join(c['used_by'])})*")

# --- Approval queue ----------------------------------------------------------------------
with tab_queue:
    st.subheader("Pending officer approval")
    items = api("GET", "/queue") or []
    pending = [i for i in items if i["status"] in ("PENDING_APPROVAL", "REJECTED", "NEEDS_OFFICER")]
    if not pending:
        st.info("Nothing waiting for approval.")
    for i in pending:
        target = i["product_id"] or i["action_id"]
        with st.container(border=True):
            st.markdown(f"**{i['item_id']}** · {i['kind']} · **{target}** · customer {i['customer_id']} "
                        f"· `{i['status']}` · revisions {i['revision_count']}")
            if i.get("reject_reason"):
                st.caption(f"Last rejection: {i['reject_reason']}")
            st.json(i["payload"], expanded=False)
            c1, c2, c3 = st.columns([1, 1, 3])
            if i["pdf_path"]:
                pdf = api("GET", f"/queue/{i['item_id']}/pdf")
                if pdf:
                    c1.download_button("PDF", pdf, file_name=f"{i['item_id']}.pdf", key=f"pdf_{i['item_id']}")
            if i["status"] == "PENDING_APPROVAL":
                if c2.button("Approve", key=f"ok_{i['item_id']}", type="primary"):
                    api("POST", f"/queue/{i['item_id']}/approve", json={"officer_id": OFFICER})
                    st.rerun()
                reason = c3.text_input("Reject reason", key=f"why_{i['item_id']}")
                if c3.button("Reject", key=f"no_{i['item_id']}") and reason:
                    out = api("POST", f"/queue/{i['item_id']}/reject", json={"officer_id": OFFICER, "reason": reason})
                    if out:
                        st.info(out["reply"]["to_officer"])
                    st.rerun()
            elif i["status"] in ("REJECTED", "NEEDS_OFFICER"):
                # Officer fixes it directly (always available; the only path in rules-only mode)
                with st.expander("Edit and resubmit for approval",
                                 expanded=i["status"] == "NEEDS_OFFICER" or not health.get("llm_configured")):
                    locked = {"customer_id", "form", "prepared_on", "aadhaar_masked", "account_no_masked",
                              "mobile_masked"}  # identity fields come from the bank record
                    editable = [k for k in i["payload"] if k not in locked] or list(i["payload"])
                    field = st.selectbox("Field", editable, key=f"rf_{i['item_id']}")
                    value = st.text_input("New value", value=str(i["payload"].get(field) or ""),
                                          key=f"rv_{i['item_id']}_{field}")
                    if st.button("Save revision", key=f"rs_{i['item_id']}"):
                        out = api("POST", f"/queue/{i['item_id']}/revise",
                                  json={"officer_id": OFFICER, "changes": {field: value},
                                        "explanation": f"officer corrected {field}"})
                        if out:
                            st.rerun()

    done = [i for i in items if i["status"] == "SUBMITTED"]
    if done:
        st.subheader("Approved & submitted (mock)")
        st.dataframe([{k: i[k] for k in ("item_id", "customer_id", "kind", "product_id", "action_id",
                                         "decided_by", "decided_at")} for i in done], use_container_width=True)

# --- Dashboard ------------------------------------------------------------------------------
with tab_dash:
    d = api("GET", "/dashboard") or {}
    if d:
        k = st.columns(6)
        k[0].metric("Customers screened", d["customers_screened"])
        k[1].metric("Products eligible", d["products_eligible"])
        k[2].metric("Pending approval", d["items_pending"])
        k[3].metric("Approved", d["items_approved"])
        k[4].metric("Rejected", d["items_rejected"])
        k[5].metric("Escalated to officer", d["items_escalated"])
        if d.get("sessions_by_state"):
            st.caption("Sessions by state: " + " · ".join(f"{s} {n}" for s, n in d["sessions_by_state"].items()))
        cols = st.columns(3)
        panels = [("Blocked by prerequisite", "blocked_by_action"),
                  ("Top not-eligible rules", "not_eligible_reasons"),
                  ("Top missing documents", "top_missing_docs")]
        for col, (title, key) in zip(cols, panels, strict=True):
            col.markdown(f"**{title}**")
            if d[key]:
                col.bar_chart(d[key], horizontal=True, height=220)
            else:
                col.caption("none")
    st.subheader("Audit log")
    rows = api("GET", "/audit", params={"limit": 200}) or []
    st.dataframe([{**{k: r[k] for k in ("ts", "session_id", "actor", "event")},
                   "payload": json.dumps(r["payload"], ensure_ascii=False)[:200]} for r in rows],
                 use_container_width=True, height=400)
