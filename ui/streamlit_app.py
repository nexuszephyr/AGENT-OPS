"""Yojana Saathi officer UI.

    streamlit run ui/streamlit_app.py     (API must be running on API_URL, default :8000)

Tabs: Officer console | Approval queue | Branch dashboard.
Look and feel: .streamlit/config.toml (theme) + ui/components.py (shared components, issue #16).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests
import streamlit as st

# Bare import: `streamlit run ui/streamlit_app.py` puts ui/ on sys.path (tests do the same with
# sys.path.insert). Importing this file as `ui.streamlit_app` would raise ModuleNotFoundError.
from components import (
    COLORS,
    app_header,
    card_title,
    customer_bar,
    customer_bubble,
    customer_card,
    empty_state,
    key_values,
    kpi_tiles,
    plan_steps,
    product_grid,
    question_prompt,
    queue_badge,
    section_header,
    setup_page,
    status_legend,
    status_style,
    stepper,
)

try:  # same as app/config.py: read .env so API_URL and YS_* set there reach the UI too
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional at runtime
    pass

API = os.getenv("API_URL", "http://localhost:8000")
DEFAULT_OFFICER = os.getenv("YS_OFFICER_ID", "officer-1")  # initial "Acting as" choice
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
# Human labels for prerequisite actions, read from the same catalogue the rules engine uses.
# Optional: if the UI runs without the repo next to it, steps and blockers fall back to the raw action id.
try:
    _CATALOG = json.loads((Path(__file__).resolve().parent.parent / "app" / "rules" / "products.json")
                          .read_text(encoding="utf-8"))
    ACTION_LABELS: dict[str, str] = {k: v["label"] for k, v in _CATALOG["actions"].items()}
except (OSError, ValueError, KeyError):
    ACTION_LABELS = {}
# Why there is no question to ask, in the officer's words (reasons come from app/planner/questions.py).
NO_QUESTION_REASONS = {
    "no resolvable unknowns": "No more questions: every answer the rules need is in.",
    "question limit reached": "Question limit reached: anything still unknown is marked Verify manually.",
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
    if r.status_code == 403:  # maker-checker refusal: show the reason, not the raw response
        st.error(f"Refused: {r.json().get('detail', r.text)}")
        return None
    if r.status_code >= 400:
        st.error(f"The API returned {r.status_code}: {r.text}")
        return None
    return r.json() if "json" in r.headers.get("content-type", "") else r.content


def set_state(new_state: dict | None) -> None:
    if new_state:
        st.session_state["state"] = new_state


def label(field: str) -> str:
    return field.replace("_", " ").capitalize()


def status_changes(state: dict) -> dict[str, str]:
    """product_id -> previous status, for products whose status changed since this session was last drawn.

    The highlight stays until the next change; the toast fires once.
    """
    sid = state["session_id"]
    current = {r["product_id"]: r["status"] for r in state["results"]}
    snapshot = st.session_state.get(f"snap_{sid}")
    if snapshot is not None and snapshot != current:
        changed = {pid: snapshot[pid] for pid in current if pid in snapshot and snapshot[pid] != current[pid]}
        st.session_state[f"changed_{sid}"] = changed
        names = {r["product_id"]: r["name"] for r in state["results"]}
        for pid, old in changed.items():
            st.toast(f"{names[pid]}: {status_style(old).label} → {status_style(current[pid]).label}",
                     icon=":material/sync:")
    st.session_state[f"snap_{sid}"] = current
    return st.session_state.get(f"changed_{sid}", {})


# --- header -----------------------------------------------------------------------
health = api("GET", "/health") or {}
officers = {o["officer_id"]: o for o in api("GET", "/officers") or []}
header = st.container()
_, col_officer = st.columns([3, 1])
ids = list(officers) or [DEFAULT_OFFICER]
OFFICER = col_officer.selectbox(
    "Acting as", ids, index=ids.index(DEFAULT_OFFICER) if DEFAULT_OFFICER in ids else 0,
    format_func=lambda oid: officers[oid]["name"] if oid in officers else oid, key="officer_id",
    help="Maker-checker: the Banking Correspondent prepares, the Branch Manager approves or rejects.")
me = officers.get(OFFICER, {"name": OFFICER, "role": ""})
with header:
    app_header(llm_configured=bool(health.get("llm_configured")), provider=health.get("provider"),
               officer=f"{me['name']} ({me['role']})" if me["role"] else me["name"], branch=BRANCH)


def officer_name(officer_id: str | None) -> str:
    return officers.get(officer_id or "", {}).get("name", officer_id or "unknown")


# A checker who prepares or revises an item becomes its maker, and with one checker nobody could decide it.
MAKER_ONLY = (None if me["role"] != "checker"
              else "The Branch Manager approves. Switch \"Acting as\" → Banking Correspondent to do this.")


def decide_block(item: dict) -> str | None:
    """Why the current officer can't approve/reject this item (mirrors app/queue.py), or None."""
    if me["role"] != "checker":
        return f"{me['name']} prepares items; only the Branch Manager can approve or reject. Switch \"Acting as\"."
    if item.get("prepared_by") == OFFICER:
        return "You prepared this item, so you can't approve or reject it (maker-checker)."
    return None

tab_console, tab_queue, tab_dash = st.tabs(["Officer console", "Approval queue", "Branch dashboard"])

# --- Officer console (issue #17) --------------------------------------------------------
with tab_console:
    state = st.session_state.get("state")
    if not state:
        section_header("Choose a customer", "Starting a session screens them for all seven schemes at once.")
        customers = api("GET", "/customers") or []
        if not customers:
            empty_state("No customers found", "Seed the demo data with python -m app.data.seed --reset.")
        if MAKER_ONLY:
            st.caption(f"🔒 {MAKER_ONLY}")
        for start in range(0, len(customers), 3):
            for col, c in zip(st.columns(3), customers[start:start + 3], strict=False):
                with col:
                    if customer_card(c, blocked=MAKER_ONLY):
                        new = api("POST", "/sessions", json={"customer_id": c["customer_id"], "officer_id": OFFICER})
                        if new:
                            set_state(new)
                            st.rerun()
    else:
        state = api("GET", f"/sessions/{state['session_id']}/state") or state
        set_state(state)
        sid = state["session_id"]
        bar, switch = st.columns([4, 1], vertical_alignment="center")
        with bar:
            customer_bar(state["profile"])
        if switch.button("Change customer", width="stretch"):
            st.session_state.pop("state", None)
            st.rerun()
        changed = status_changes(state)
        left, right = st.columns([1, 1.15], gap="large")

        with right:
            section_header("Products", "Decided by the rules engine. Open a card to see the cited rules.")
            with st.popover("What the statuses mean"):
                status_legend()
            product_grid(state["results"], changed, ACTION_LABELS)

        with left:
            section_header("Conversation", f"Session {sid} · stage: {label(state['state'])}")
            shown = 0
            for m in state["messages"][-10:]:
                if m["role"] == "user":
                    if m["content"].startswith("The officer rejected"):
                        continue  # internal revision prompt, not something the officer typed
                    with st.chat_message("user", avatar=":material/badge:"):
                        st.caption("Officer")
                        st.write(m["content"])
                    shown += 1
                    continue
                try:
                    rep = json.loads(m["content"])
                except json.JSONDecodeError:
                    rep = {"to_officer": m["content"]}
                with st.chat_message("assistant", avatar=":material/support_agent:"):
                    st.caption("Yojana Saathi" + (" (rules only)" if rep.get("mode") == "rules_only" else ""))
                    st.write(rep.get("to_officer", ""))
                    if rep.get("to_customer"):
                        customer_bubble(rep["to_customer"].get("lang"), rep["to_customer"].get("text", ""))
                shown += 1
            # The current question, pinned above the input. Works with or without the LLM.
            nq = state["next_question"]["question"]
            if not shown and nq:
                st.caption("Ask the question below and record the answer, or type what the customer said.")
            if nq:
                with st.container(border=True, key="ys-question"):
                    question_prompt(nq["text_en"], nq.get("text_kn"))
                    if nq.get("officer_note"):
                        st.caption(nq["officer_note"])
                    # Nothing is preselected: a field the officer doesn't set stays unknown (rule 4).
                    facts = {}
                    for field, kind in QUESTION_FACT_INPUTS.get(nq["question_id"], []):
                        if kind == "bool":
                            ans = st.radio(label(field), ["Yes", "No"], index=None, horizontal=True,
                                           key=f"f_{sid}_{field}")
                            if ans is not None:
                                facts[field] = ans == "Yes"
                        else:
                            num = st.number_input(label(field), min_value=0, step=1, value=None,
                                                  key=f"f_{sid}_{field}")
                            if num is not None:
                                facts[field] = int(num)
                    b1, b2 = st.columns(2)
                    if b1.button("Record answer", type="primary", disabled=not facts, width="stretch"):
                        out = api("POST", f"/sessions/{sid}/facts",
                                  json={"facts": facts, "question_id": nq["question_id"]})
                        if out:
                            set_state(out["state"])
                            rej = out["result"].get("rejected_fields")
                            if rej:  # stay on this run so the officer sees why
                                st.warning("Not recorded: " + "; ".join(f"{r['field']} ({r['reason']})" for r in rej))
                            else:
                                st.rerun()
                    # Resolves the question with no answer: its products go to VERIFY_MANUALLY.
                    if b2.button("Customer doesn't know", width="stretch"):
                        out = api("POST", f"/sessions/{sid}/facts",
                                  json={"facts": {}, "question_id": nq["question_id"]})
                        if out:
                            set_state(out["state"])
                            st.rerun()
            elif state["next_question"].get("reason"):
                st.caption(NO_QUESTION_REASONS.get(state["next_question"]["reason"],
                                                   f"No more questions: {state['next_question']['reason']}."))

            text = st.chat_input("Type what the customer said, or an instruction")
            if text:
                out = api("POST", f"/sessions/{sid}/message", json={"text": text})
                if out:
                    set_state(out["state"])
                    st.rerun()

            section_header("Plan", "In the order the steps must happen. Ticks appear as you approve them.")
            steps = plan_steps(state, ACTION_LABELS)
            if steps:
                stepper(steps)
            else:
                empty_state("Nothing to prepare yet", "Steps appear once a product is eligible.")
            if state["plan"]["checklist"]:
                st.markdown("**Documents the customer should bring**")
                for c in state["plan"]["checklist"]:
                    st.markdown(f"- {c['label']} *(for {', '.join(c['used_by'])})*")

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
                        out = api("POST", f"/sessions/{sid}/act", json={"extra_fields": extra})
                        if out:
                            for o in out["outcomes"]:
                                if o.get("error"):
                                    st.warning(f"Step {o.get('step')}: {o['error']}")
                                elif o.get("status") == "NEEDS_INFO":
                                    st.warning(f"Step {o.get('step')} ({o.get('product_id')}) needs: "
                                               + ", ".join(o.get("missing_mandatory", [])))
                            set_state(out["state"])
                            st.rerun()

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
                               f" · revisions {i['revision_count']}", queue_badge(i["status"]),
                       detail=f"Prepared by {officer_name(i.get('prepared_by'))}")
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
                blocked = decide_block(i)
                if blocked:
                    st.caption(f"🔒 {blocked}")
                if (c2.button("Approve", key=f"ok_{i['item_id']}", type="primary", width="stretch",
                              disabled=bool(blocked), help=blocked)
                        and api("POST", f"/queue/{i['item_id']}/approve", json={"officer_id": OFFICER})):
                    st.rerun()  # on a refusal, stay so the error stays visible
                # A form submits the typed reason with the click, so the first click always counts
                # (a disabled button only enables after the text box blurs). The API also requires a reason.
                with c3.form(key=f"rej_{i['item_id']}", border=False):
                    reason = st.text_input("Reason for rejecting", key=f"why_{i['item_id']}",
                                           placeholder="e.g. Nominee name is Anjali, not Anjeli")
                    rejected = st.form_submit_button("Reject", disabled=bool(blocked), help=blocked)
                if rejected:
                    if not reason.strip():
                        st.warning("Type a reason first, so the agent knows what to fix.")
                    else:
                        out = api("POST", f"/queue/{i['item_id']}/reject",
                                  json={"officer_id": OFFICER, "reason": reason})
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
                    st.caption(f"🔒 {MAKER_ONLY}" if MAKER_ONLY else
                               "Saving makes you this item's preparer, so the Branch Manager must approve it.")
                    if st.button("Save and send back", key=f"rs_{i['item_id']}", type="primary",
                                 disabled=bool(MAKER_ONLY), help=MAKER_ONLY):
                        out = api("POST", f"/queue/{i['item_id']}/revise",
                                  json={"officer_id": OFFICER, "changes": {field: value},
                                        "explanation": f"officer corrected {field}"})
                        if out:
                            st.rerun()

    done = [i for i in items if i["status"] == "SUBMITTED"]
    if done:
        section_header("Approved and submitted", "Submission is mocked in this prototype.")
        st.dataframe([{"Item": i["item_id"], "Customer": i["customer_id"], "Kind": label(i["kind"]),
                       "Product / action": i["product_id"] or i["action_id"],
                       "Prepared by": officer_name(i.get("prepared_by")),
                       "Approved by": officer_name(i["decided_by"]), "Approved at": i["decided_at"]}
                      for i in done], width="stretch", hide_index=True)

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
