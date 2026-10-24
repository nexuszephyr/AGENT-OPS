"""Yojana Saathi officer UI.

    streamlit run ui/streamlit_app.py     (API must be running on API_URL, default :8000)

Tabs: Officer console | Branch scan | Approval queue | Branch dashboard.
Look and feel: .streamlit/config.toml (theme) + ui/components.py (shared components, issue #16).
"""
from __future__ import annotations

import json
import os
import threading
from collections import Counter
from pathlib import Path

import requests
import streamlit as st

# Bare import: `streamlit run ui/streamlit_app.py` puts ui/ on sys.path (tests do the same with
# sys.path.insert). Importing this file as `ui.streamlit_app` would raise ModuleNotFoundError.
from components import (
    activity_feed,
    api_error_text,
    app_header,
    approval_chain_html,
    call_row,
    card_title,
    count_chart,
    customer_bar,
    customer_bubble,
    customer_card,
    empty_state,
    event_datetime,
    event_time,
    field_label,
    filter_call_list,
    filter_results,
    format_inr,
    format_minutes,
    funnel_chart,
    funnel_sentence,
    key_values,
    kpi_tiles,
    panel_counts,
    pdf_first_page_png,
    pipeline,
    plan_steps,
    prep_fields,
    product_grid,
    product_status_chart,
    question_prompt,
    queue_badge,
    queue_group_header,
    readable,
    rejection_note,
    result_table,
    revision_diff,
    section_header,
    setup_page,
    stage_chips,
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
    PRODUCT_NAMES: dict[str, str] = {p["id"]: p["name"] for p in _CATALOG["products"]}
except (OSError, ValueError, KeyError):
    ACTION_LABELS, PRODUCT_NAMES = {}, {}
# Why there is no question to ask, in the officer's words (reasons come from app/planner/questions.py).
NO_QUESTION_REASONS = {
    "no resolvable unknowns": "No more questions: every answer the rules need is in.",
    "question limit reached": "Question limit reached: anything still unknown is marked Verify manually.",
}
# Identity fields come from the bank record: hidden from the officer's edit picker.
LOCKED_FIELDS = {"customer_id", "form", "prepared_on", "aadhaar_masked", "account_no_masked", "mobile_masked"}

setup_page()


def api(method: str, path: str, quiet: bool = False, spinner: str | None = None, **kw):
    """Call the API. Errors are shown on the page as a sentence (never the raw response) and return None;
    `quiet=True` returns None silently (for an expected miss, e.g. GET /scan/latest before any scan).
    `spinner` shows a loading message while a slow call runs (#24)."""
    try:
        if spinner:
            with st.spinner(spinner):
                r = requests.request(method, f"{API}{path}", timeout=120, **kw)
        else:
            r = requests.request(method, f"{API}{path}", timeout=120, **kw)
    except requests.ConnectionError:
        api_down()
    except requests.Timeout:
        st.error("The API took too long to answer. Try again; if it keeps happening, restart the API.",
                 icon=":material/hourglass_disabled:")
        return None
    if method != "GET":  # any write (even a refused one) adds audit rows / sessions: drop the cached facets
        audit_facets.clear()
    if r.status_code >= 400:
        if r.status_code == 403:  # maker-checker refusal: the reason, as a sentence
            st.error(f"Refused: {api_error_text(403, r.text)}", icon=":material/gpp_bad:")
        elif not quiet:
            st.error(api_error_text(r.status_code, r.text), icon=":material/error:")
        return None
    return r.json() if "json" in r.headers.get("content-type", "") else r.content


def api_down() -> None:
    """The API isn't running: say what to do, offer a retry, and draw nothing else."""
    empty_state("Can't reach the Yojana Saathi API",
                f"Start it with: uvicorn app.api.main:app (expected at {API}), then retry.")
    st.button("Retry", type="primary", icon=":material/refresh:")
    st.stop()


def set_state(new_state: dict | None) -> None:
    if new_state:
        st.session_state["state"] = new_state


def flash(msg: str, icon: str = ":material/check_circle:") -> None:
    """A confirmation toast that survives the st.rerun() after an action."""
    st.session_state.setdefault("flash", []).append((msg, icon))


@st.cache_data(ttl=10)
def audit_facets() -> dict:
    """Distinct actors / events / sessions for the audit filters (cached: they change slowly)."""
    return api("GET", "/audit/facets") or {}


def label(field: str) -> str:
    return field_label(field)


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


def last_reply_mode(state: dict) -> str | None:
    """'agent' or 'rules_only' for the agent's latest reply (it falls back to rules-only when the LLM fails)."""
    last = next((m for m in reversed(state.get("messages") or []) if m["role"] != "user"), None)
    try:
        return json.loads(last["content"]).get("mode") if last else None
    except (ValueError, AttributeError):
        return None


# Example values for the details "Prepare all steps" asks for (fields from each plan step's missing_mandatory).
PREP_HINTS = {"nominee_name": "e.g. Anjali", "nominee_relation": "e.g. daughter", "pension_amount": "1000 to 5000",
              "girl_child_name": "e.g. Meera", "crop_name": "e.g. ragi", "season": "kharif or rabi"}


# --- live agent turn (issue #18) ---------------------------------------------------------
# POST /message blocks until the agent's turn ends (tens of seconds with a real LLM). It runs in a background
# thread instead, so the page can poll GET /timeline every second and show each tool call as it happens.
# The thread only does HTTP and fills a dict; it never touches Streamlit.

def start_turn(sid: str, text: str) -> None:
    box: dict = {"done": False, "out": None, "error": None}

    def work() -> None:
        try:
            r = requests.post(f"{API}/sessions/{sid}/message", json={"text": text}, timeout=300)
            if r.status_code < 400:
                box["out"] = r.json()
            else:
                box["error"] = f"The API returned {r.status_code}: {r.text}"
        except requests.RequestException as e:
            box["error"] = f"Can't reach the API at {API}: {e}"
        finally:
            box["done"] = True

    threading.Thread(target=work, daemon=True, name=f"agent-turn-{sid}").start()
    st.session_state["turn"] = {"sid": sid, "text": text, "box": box}


def turn_running(sid: str) -> bool:
    t = st.session_state.get("turn")
    return bool(t and t["sid"] == sid and not t["box"]["done"])


def collect_turn(sid: str) -> bool:
    """If this session's turn has finished, take its result. Returns True when it did."""
    t = st.session_state.get("turn")
    if not (t and t["sid"] == sid and t["box"]["done"]):
        return False
    st.session_state.pop("turn")
    if t["box"]["out"]:
        set_state(t["box"]["out"]["state"])
    if t["box"]["error"]:
        st.session_state["turn_error"] = t["box"]["error"]
    return True


def pipeline_notes(state: dict) -> dict[str, str]:
    """One line per stage, in the officer's terms."""
    items = state.get("queue_items") or []
    eligible = sum(r["status"] in ("ELIGIBLE", "ELIGIBLE_BLOCKED") for r in state.get("results") or [])
    pending = sum(i["status"] == "PENDING_APPROVAL" for i in items)
    submitted = sum(i["status"] == "SUBMITTED" for i in items)
    asked = state.get("questions_asked", 0)
    return {
        "ingest": f"{state.get('profile', {}).get('name', 'Customer')}: bank record + interview",
        "decide": f"{eligible} eligible, {asked} question{'s' if asked != 1 else ''} asked",
        "act": f"{pending} waiting for approval" if pending else f"{len(items)} prepared",
        "report": f"{submitted} approved and submitted",
    }


def _pipeline_body(sid: str) -> None:
    t = api("GET", f"/sessions/{sid}/timeline")
    if not t:
        return
    notes = pipeline_notes(st.session_state.get("state") or {})
    active_tool = None
    if t["running"]:  # mid-turn: the page state is stale, so count from the live timeline instead
        events = t["events"]
        last_msg = max((i for i, e in enumerate(events) if e["kind"] == "message"), default=-1)
        turn = events[last_msg + 1:]
        prepared = sum(e["tool"] in ("fill_form", "raise_request") and e["tone"] == "ok" for e in turn)
        notes["act"] = f"{prepared} prepared in this turn"
        active_tool = next((e["tool"] for e in reversed(turn) if e["tool"]), None)
    pipeline(t["stages"], notes, active_tool)


@st.fragment(run_every=1.0)
def pipeline_live(sid: str) -> None:
    _pipeline_body(sid)


def _activity_body(sid: str, running: bool) -> None:
    t = api("GET", f"/sessions/{sid}/timeline")
    if not t:
        return
    events = t["events"]
    if running:
        last_msg = max((i for i, e in enumerate(events) if e["kind"] == "message"), default=-1)
        calls = sum(e["kind"] == "tool" for e in events[last_msg + 1:])
        st.info(f"The agent is working: {calls} tool call{'s' if calls != 1 else ''} so far. "
                "Each one appears below as it happens.", icon=":material/progress_activity:")
    show_all = len(events) > 10 and st.toggle(f"Show all {len(events)} events", key=f"all_{sid}")
    if events:
        activity_feed(events, None if show_all else 10)
    else:
        st.caption("Nothing yet.")


@st.fragment(run_every=1.0)
def activity_live(sid: str) -> None:
    if collect_turn(sid):
        st.rerun()  # the turn finished: redraw the whole page with the new state
    _activity_body(sid, running=True)


# --- header -----------------------------------------------------------------------
health = api("GET", "/health") or {}
officers = {o["officer_id"]: o for o in api("GET", "/officers") or []}
header = st.container()
_, col_officer = st.columns([2.2, 1])
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


CHECKER_NAME = next((o["name"] for o in officers.values() if o.get("role") == "checker"), "Branch Manager")


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

# The tabs track state (key + on_change) so a click elsewhere can open the console: "Start session" on a scan row
# sets goto_tab, and it is applied here, before the tabs are drawn (#38).
if goto := st.session_state.pop("goto_tab", None):
    st.session_state["nav"] = goto
tab_console, tab_scan, tab_queue, tab_dash = st.tabs(
    ["Officer console", "Branch scan", "Approval queue", "Branch dashboard"], key="nav", on_change="rerun")

# --- Officer console (issue #17) --------------------------------------------------------
with tab_console:
    state = st.session_state.get("state")
    if not state:
        section_header("Choose a customer", "Starting a session screens them for all seven schemes at once.")
        customers = api("GET", "/customers", params={"segment": "demo"}) or []  # branch book: scan tab (#38)
        if not customers:
            empty_state("No customers found", "Seed the demo data with python -m app.data.seed --reset.")
        if MAKER_ONLY:
            st.caption(f"🔒 {MAKER_ONLY}")
        for start in range(0, len(customers), 3):
            for col, c in zip(st.columns(3), customers[start:start + 3], strict=False):
                with col:
                    if customer_card(c, blocked=MAKER_ONLY):
                        new = api("POST", "/sessions", json={"customer_id": c["customer_id"], "officer_id": OFFICER},
                                  spinner=f"Screening {c.get('name')} for all seven schemes…")
                        if new:
                            set_state(new)
                            st.rerun()
    else:
        collect_turn(state["session_id"])  # a turn that finished between reruns
        state = api("GET", f"/sessions/{st.session_state['state']['session_id']}/state") or state
        set_state(state)
        sid = state["session_id"]
        running = turn_running(sid)
        if err := st.session_state.pop("turn_error", None):
            st.error(err, icon=":material/error:")
        if health.get("llm_configured") and last_reply_mode(state) == "rules_only":
            st.warning("The language model didn't answer, so the last reply came from the rules engine (rules-only "
                       "mode). Statuses, the plan and the question form still work; try the chat again later.",
                       icon=":material/cloud_off:")
        bar, switch = st.columns([4, 1], vertical_alignment="center")
        with bar:
            customer_bar(state["profile"])
        if switch.button("Change customer", key="change_customer", width="stretch"):
            st.session_state.pop("state", None)
            st.rerun()
        # CGI's pipeline, live: which stage the session is in and which tools the agent used in each.
        if running:
            pipeline_live(sid)
        else:
            _pipeline_body(sid)
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
            turn = st.session_state.get("turn")
            if running and turn and not any(m["role"] == "user" and m["content"] == turn["text"]
                                            for m in state["messages"][-2:]):
                with st.chat_message("user", avatar=":material/badge:"):  # sent, reply on its way
                    st.caption("Officer")
                    st.write(turn["text"])
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
                    if b1.button("Record answer", key="record_answer", type="primary", disabled=running or not facts,
                                 width="stretch"):
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
                    if b2.button("Doesn't know", key="dont_know", disabled=running, width="stretch",
                                 help="The customer doesn't know: the products that need this answer go to "
                                      "Verify manually"):
                        out = api("POST", f"/sessions/{sid}/facts",
                                  json={"facts": {}, "question_id": nq["question_id"]})
                        if out:
                            set_state(out["state"])
                            st.rerun()
            elif state["next_question"].get("reason"):
                st.caption(NO_QUESTION_REASONS.get(state["next_question"]["reason"],
                                                   f"No more questions: {state['next_question']['reason']}."))

            text = st.chat_input("The agent is working…" if running else
                                 "Type what the customer said, or an instruction", disabled=running)
            if text and not running:
                start_turn(sid, text)
                st.rerun()

            section_header("Agent activity", "What the agent and the officer did, newest first. "
                                             "Open an event to see what went in and what came out.")
            if running:
                activity_live(sid)
            else:
                _activity_body(sid, running=False)

            section_header("Plan", "In the order the steps must happen. Ticks appear as they are approved.")
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
                needed = prep_fields(state)
                st.caption("Queues every step that is ready, for the Branch Manager to approve. Details that several "
                           "forms share are asked once." if needed else "Queues every step that is ready, for the "
                           "Branch Manager to approve. No extra details are needed.")
                # a form: every typed value is submitted with the first click (no raw JSON to type, #24)
                with st.form(key=f"prep_{sid}", border=False):
                    values: dict = {}
                    cols = st.columns(2)
                    for n, (f, pids) in enumerate(needed.items()):
                        help_text = "For " + ", ".join(PRODUCT_NAMES.get(p, p) for p in pids)
                        with cols[n % 2]:
                            if f.endswith("_dob"):
                                values[f] = st.date_input(field_label(f), value=None, key=f"prep_{sid}_{f}",
                                                          help=help_text, format="YYYY-MM-DD")
                            else:
                                values[f] = st.text_input(field_label(f), key=f"prep_{sid}_{f}", help=help_text,
                                                          placeholder=PREP_HINTS.get(f, ""))
                    go = st.form_submit_button("Prepare all steps", key="prepare_all", type="primary", disabled=running)
                if go:
                    extra: dict = {}
                    for f, pids in needed.items():
                        v = values.get(f)
                        v = v.isoformat() if hasattr(v, "isoformat") else (v or "").strip()
                        if v:
                            for pid in pids:
                                extra.setdefault(pid, {})[f] = int(v) if f == "pension_amount" and v.isdigit() else v
                    out = api("POST", f"/sessions/{sid}/act", json={"extra_fields": extra},
                              spinner="Preparing the forms and service requests…")
                    if out:
                        missing = [f"{o.get('product_id')}: " + ", ".join(field_label(m) for m in
                                                                          o.get("missing_mandatory", []))
                                   for o in out["outcomes"] if o.get("status") == "NEEDS_INFO"]
                        errors = [f"Step {o.get('step')}: {o['error']}" for o in out["outcomes"] if o.get("error")]
                        set_state(out["state"])
                        queued = sum(o.get("status") == "PENDING_APPROVAL" for o in out["outcomes"])
                        flash(f"{queued} step{'s' if queued != 1 else ''} waiting for the Branch Manager's approval."
                              + (" Still needs details: " + "; ".join(missing) + "." if missing else ""),
                              ":material/playlist_add_check:")
                        for e in errors:
                            flash(e, ":material/error:")
                        st.rerun()

# --- Approval queue (issue #19) -------------------------------------------------------------
OPEN_ITEM = ("NEEDS_OFFICER", "REJECTED", "REVISED", "PENDING_APPROVAL")
# Within a customer: escalated, then revised (has a diff to check), then rejected, then the rest.
_ITEM_ORDER = {"NEEDS_OFFICER": 0, "REJECTED": 2, "REVISED": 1, "PENDING_APPROVAL": 3}


@st.cache_data(show_spinner=False, max_entries=200)
def pdf_preview(pdf: bytes) -> tuple[bytes, int]:
    return pdf_first_page_png(pdf)


def item_title(i: dict) -> str:
    return PRODUCT_NAMES.get(i["product_id"] or "") or ACTION_LABELS.get(i["action_id"] or "") \
        or i["product_id"] or i["action_id"]


def item_priority(i: dict, position: dict[str, int]) -> tuple:
    """Escalated, revised (a diff to check), rejected, pending; ties keep queue order, which is plan order
    (e.g. Aadhaar seeding before the PM-KISAN form it unlocks)."""
    revised = i["status"] == "PENDING_APPROVAL" and i["revision_count"] > 0
    return (0 if i["status"] == "NEEDS_OFFICER" else 1 if revised else _ITEM_ORDER.get(i["status"], 3) + 1,
            position.get(i["item_id"], 0))


def queue_item(i: dict) -> None:
    """One item: PDF page preview on the left; what it is, why it came back, what changed and the actions on the
    right, so the checker decides without scrolling (#19 'done when')."""
    iid, title = i["item_id"], item_title(i)
    hist = api("GET", f"/queue/{iid}/history") if (i["revision_count"] or i.get("reject_reason")) else None
    key = f"ys-escalated-{iid}" if i["status"] == "NEEDS_OFFICER" else f"qi-{iid}"
    with st.container(border=True, key=key):
        left, right = st.columns([0.55, 2.8], gap="medium")
        with left:
            pdf = api("GET", f"/queue/{iid}/pdf") if i.get("pdf_path") else None
            if isinstance(pdf, bytes | bytearray):
                try:
                    png, pages = pdf_preview(bytes(pdf))
                    st.image(png, width=170)  # the real PDF page, as a thumbnail
                    # "Zoom", not "View page": a short label fits the preview column on a tablet (#24)
                    with st.popover("Zoom", icon=":material/zoom_in:", width=170,
                                    help="Page 1 of the form at a readable size"):
                        st.image(png, width=520, caption=f"{iid}: page 1 of {pages}")
                except Exception:  # noqa: BLE001 - a preview problem must never hide the actions
                    st.caption("Preview unavailable: download the PDF instead.")
                st.download_button("PDF", pdf, file_name=f"{iid}.pdf", key=f"pdf_{iid}",
                                   icon=":material/download:", width=170, help="Download the form as PDF")
            else:
                empty_state("No PDF", "This item has no form document.")
        with right:
            # the approval chain (who prepared it, who it waits on) sits where the status badge was: same facts, and
            # the card stays one line shorter, so Approve is still above the fold at 1366x768 (#19, #24)
            card_title(title, f"{iid} · revision {i['revision_count']}",
                       (queue_badge(i["status"]) if i["status"] == "NEEDS_OFFICER" else "")
                       + approval_chain_html(i, officer_name, CHECKER_NAME))
            rej = (hist or {}).get("rejection")
            if rej:
                rejection_note(rej.get("reason") or "", officer_name(rej.get("officer_id")))
            if (hist or {}).get("diff"):
                revision_diff(hist["diff"], label)
            elif i["status"] in ("REJECTED", "NEEDS_OFFICER"):
                st.caption("Not changed yet. " + ("The agent tries a fix first; the Banking Correspondent can also "
                                                    "edit it below." if health.get("llm_configured") else
                                                    "The Banking Correspondent edits it below."))
            queue_actions(i, title)
            with st.expander("All form fields"):
                key_values(i["payload"])


def queue_actions(i: dict, title: str) -> None:
    iid = i["item_id"]
    if i["status"] == "PENDING_APPROVAL":
        blocked = decide_block(i)
        a1, a2, _ = st.columns([1.1, 1.1, 1.6])
        if a1.button("Approve", key=f"ok_{iid}", type="primary", icon=":material/check:", width="stretch",
                     disabled=bool(blocked), help=blocked):
            out = api("POST", f"/queue/{iid}/approve", json={"officer_id": OFFICER}, spinner=f"Approving {title}…")
            if out:  # on a refusal, stay so the error stays visible
                facts = {k: v for k, v in (out.get("applied") or {}).items() if k != "enrollments"}
                applied = readable(facts) if facts else ""
                flash(f"Approved {title} ({iid}): submitted." + (f" Customer record updated: {applied}." if applied
                                                                  else ""))
                st.rerun()
        with a2.popover("Reject…", icon=":material/close:", disabled=bool(blocked), help=blocked, width="stretch"):
            # A form submits the typed reason with the click, so the first click always counts.
            with st.form(key=f"rej_{iid}", border=False):
                reason = st.text_input("Reason for rejecting", key=f"why_{iid}",
                                       placeholder="e.g. Nominee name is Anjali, not Anjeli")
                rejected = st.form_submit_button("Reject with this reason", type="primary")
            if rejected:
                if not reason.strip():
                    st.warning("Type a reason first, so the agent knows what to fix.")
                else:
                    out = api("POST", f"/queue/{iid}/reject", json={"officer_id": OFFICER, "reason": reason},
                              spinner="Sending it back…")
                    if out:
                        now = (out.get("item") or {}).get("status")
                        flash(f"Rejected {title} ({iid}). " + (
                            "The agent revised it: it is back for approval." if now == "PENDING_APPROVAL" else
                            "Escalated: an officer must fix it." if now == "NEEDS_OFFICER" else
                            "It waits for the Banking Correspondent to fix it."), ":material/undo:")
                        st.rerun()
        if blocked:
            st.caption(f"🔒 {blocked}")
    elif i["status"] in ("REJECTED", "NEEDS_OFFICER"):
        a1, _ = st.columns([1.4, 2.4])
        with a1.popover("Edit and send back…", icon=":material/edit:", disabled=bool(MAKER_ONLY), help=MAKER_ONLY,
                        width="stretch"):
            editable = [k for k in i["payload"] if k not in LOCKED_FIELDS] or list(i["payload"])
            field = st.selectbox("Field", editable, key=f"rf_{iid}", format_func=label)
            # a form, like Reject: the typed value is submitted with the first click
            with st.form(key=f"edit_{iid}_{field}", border=False):
                value = st.text_input("New value", value=str(i["payload"].get(field) or ""), key=f"rv_{iid}_{field}")
                st.caption("Saving makes you this item's preparer, so the Branch Manager must approve it.")
                save = st.form_submit_button("Save and send back", type="primary")
            if save:
                out = api("POST", f"/queue/{iid}/revise",
                          json={"officer_id": OFFICER, "changes": {field: value},
                                "explanation": f"officer corrected {field}"})
                if out:
                    flash(f"Sent {title} ({iid}) back for approval "
                          f"(revision {(out.get('item') or {}).get('revision_count', '')}).", ":material/send:")
                    st.rerun()
        if MAKER_ONLY:
            st.caption(f"🔒 {MAKER_ONLY}")


with tab_queue:
    for msg, icon in st.session_state.pop("flash", []):
        st.toast(msg, icon=icon)
    items = api("GET", "/queue") or []
    open_items = [i for i in items if i["status"] in OPEN_ITEM]
    # Compact on purpose: at 1366x768 the first item's diff and Approve must be visible without scrolling (#19).
    section_header("Approval queue", f"{len(open_items)} open. The Branch Manager decides; nobody approves an item "
                                     "they prepared (maker-checker).")
    if not open_items:
        empty_state("Nothing waiting for approval",
                    "Prepared forms and service requests appear here. Start a session in the Officer console.")

    escalated = [i for i in open_items if i["status"] == "NEEDS_OFFICER"]
    if escalated:
        st.markdown("**Needs an officer now**: these hit the revision limit.")
        for i in escalated:
            queue_item(i)

    names = {c["customer_id"]: c for c in (api("GET", "/customers") or [])}
    groups: dict[str, list[dict]] = {}
    for i in open_items:
        if i["status"] != "NEEDS_OFFICER":
            groups.setdefault(i["customer_id"], []).append(i)
    position = {i["item_id"]: n for n, i in enumerate(items)}  # GET /queue is in insertion (plan) order
    for cid in sorted(groups, key=lambda c: (min(item_priority(i, position) for i in groups[c]), c)):
        group = sorted(groups[cid], key=lambda i: item_priority(i, position))
        c = names.get(cid, {})
        queue_group_header(c.get("name", cid), f"{cid} · {len(group)} open",
                           Counter(i["status"] for i in group))
        for i in group:
            queue_item(i)

    done = [i for i in items if i["status"] == "SUBMITTED"]
    if done:
        section_header("Approved and submitted", "Submission is mocked in this prototype.")
        st.dataframe([{"Item": i["item_id"],
                       "Customer": f"{names.get(i['customer_id'], {}).get('name', i['customer_id'])} "
                                   f"({i['customer_id']})",
                       "What": item_title(i), "Prepared by": officer_name(i.get("prepared_by")),
                       "Approved by": officer_name(i["decided_by"]), "Approved at": event_datetime(i["decided_at"])}
                      for i in done], width="stretch", hide_index=True)

# --- Branch scan (issue #38) ---------------------------------------------------------------
SCAN_PAGE = 10  # rows shown before "Show all"


def start_session_from_scan(c: dict) -> None:
    new = api("POST", "/sessions", json={"customer_id": c["customer_id"], "officer_id": OFFICER},
              spinner=f"Screening {c.get('name')} for all seven schemes…")
    if new:
        set_state(new)
        st.session_state["goto_tab"] = "Officer console"
        flash(f"Session started for {c.get('name')} ({c['customer_id']}), rank {c.get('rank')} on the call list.",
              ":material/play_arrow:")
        st.rerun()


with tab_scan:
    section_header("Branch scan", "Who to call today, and why. The scan only recommends: nothing is queued and no "
                                  "customer changes until you start a session.")
    run_col, info_col = st.columns([1, 3], vertical_alignment="center")
    if (run_col.button("Run scan", key="run_scan", type="primary", icon=":material/radar:", width="stretch",
                       help="Checks every customer in the branch against all seven schemes")
            and api("POST", "/scan", json={"officer_id": OFFICER}, spinner="Scanning every customer in the branch…")):
        flash("Scan complete: the call list is up to date.", ":material/radar:")
        st.rerun()
    result = api("GET", "/scan/latest", quiet=True)
    if not result:
        empty_state("No scan yet today", "Run the scan to build the call list from every customer in the branch.")
    else:
        summary, rows = result["summary"], result["call_list"]
        info_col.caption(f"Last run at {event_time(result['generated_at'].replace('T', ' ')[:19])}: "
                         f"{result['customers_scanned']} customers scanned, {len(rows)} worth a call.")
        by_product = summary.get("opportunities_by_product") or {}
        top = max(by_product, key=lambda k: by_product[k]) if by_product else None
        seeding = (summary.get("blockers_by_action") or {}).get("AADHAAR_SEEDING_REQUEST", 0)
        unclaimed = summary.get("value_inr_unclaimed") or 0
        kpi_tiles([
            ("₹ unclaimed", format_inr(unclaimed, compact=True) if unclaimed else "—"),
            ("Customers to call", summary["customers_with_opportunities"]),
            ("PM-KISAN payments at risk", summary["urgent_count"], summary["urgent_count"] > 0),
            ("Need Aadhaar seeding", seeding, seeding > 0),
            (f"Top product: {by_product[top]} customers" if top else "Top product", top or "—"),
        ])
        # one line on purpose: keeps rank 1's Start session on screen at 1366x768
        st.caption("₹ = indicative cover or yearly benefit, not money paid out (schemes with no single value are left "
                   "out). Ranked urgent first, then by ₹.")

        # filters fold away so the top-ranked row is on screen at 1366x768 (#38 done-when: one click)
        # a constant label: a changing one would make Streamlit redraw the expander closed after every pick
        filters = st.expander("Filter the call list", icon=":material/filter_list:")
        f1, f2, f3, f4 = filters.columns([1.3, 1.3, 1.3, 0.8], vertical_alignment="bottom")
        products = f1.multiselect("Product", sorted(by_product), key="scan_products",
                                  format_func=lambda p: f"{p} · {PRODUCT_NAMES.get(p, p)}", placeholder="Any product")
        blockers = f2.multiselect("Blocker", sorted(summary.get("blockers_by_action") or {}), key="scan_blockers",
                                  format_func=lambda a: ACTION_LABELS.get(a, a), placeholder="Any blocker")
        districts = f3.multiselect("District", sorted({c.get("district") or "" for c in rows} - {""}),
                                   key="scan_districts", placeholder="Any district")
        urgent_only = f4.toggle("Urgent only", key="scan_urgent",
                                help="Only customers whose PM-KISAN payments will fail")
        shown = filter_call_list(rows, products, blockers, districts, urgent_only)
        active = [*([f"product: {', '.join(products)}"] if products else []),
                  *([f"blocker: {', '.join(ACTION_LABELS.get(a, a) for a in blockers)}"] if blockers else []),
                  *([f"district: {', '.join(districts)}"] if districts else []),
                  *(["urgent only"] if urgent_only else [])]
        if active:
            st.caption(f"Filtered by {'; '.join(active)}: {len(shown)} of {len(rows)} customers.")
        show_all = bool(st.session_state.get("scan_all")) and len(shown) > SCAN_PAGE
        if MAKER_ONLY:
            st.caption(f"🔒 {MAKER_ONLY}")
        if not shown:
            empty_state("No customers match these filters", "Clear a filter to see more of the call list.")
        for c in shown if show_all else shown[:SCAN_PAGE]:
            with st.container(border=True, key=f"scan-{c['customer_id']}"):
                info, act = st.columns([5, 1], vertical_alignment="center")
                with info:
                    call_row(c, PRODUCT_NAMES)
                if act.button("Start session", key=f"scan_start_{c['customer_id']}", type="primary" if c["rank"] == 1
                              else "secondary", icon=":material/play_arrow:", width="stretch",
                              disabled=bool(MAKER_ONLY), help=MAKER_ONLY or "Opens the officer console for them"):
                    start_session_from_scan(c)
        if len(shown) > SCAN_PAGE:
            st.toggle(f"Show all {len(shown)}" if not show_all else f"Showing all {len(shown)}", key="scan_all",
                      help=f"Only the top {SCAN_PAGE} are shown by default")

# --- Dashboard ------------------------------------------------------------------------------
with tab_dash:
    # Read-only tab (#21): one /dashboard and one /audit call per rerun, no approve/reject/act/facts calls.
    head, refresh = st.columns([5, 1], vertical_alignment="bottom")
    with head:
        section_header("Branch outcomes", "Latest session per customer · indicative values")
    if refresh.button("Refresh", key="dash_refresh", width="stretch", icon=":material/refresh:"):
        audit_facets.clear()
        st.rerun()
    d = api("GET", "/dashboard") or {}
    if d:
        kpi_tiles([
            ("Customers screened", d["customers_screened"]),
            ("Products eligible", d["products_eligible"]),
            ("Pending approval", d["items_pending"]),
            ("Approved", d["items_approved"]),
            ("Escalated to officer", d["items_escalated"], d["items_escalated"] > 0),
            ("Avg. time to approval", format_minutes(d.get("avg_minutes_to_approval"))),
        ])
        kpi_tiles([
            ("₹ unlocked", format_inr(d.get("value_inr_unlocked", 0), compact=True)),
            ("₹ eligible, not yet enrolled", format_inr(d.get("value_inr_eligible", 0), compact=True)),
            ("Rejected", d["items_rejected"], d["items_rejected"] > 0),
        ])
        st.caption("Indicative cover / annual benefit per product (not money paid out). Unlocked so far = every "
                   "approved enrolment form in this branch's data; products without a single value are left out.")

        if not d.get("customers_screened"):
            empty_state("Run a session to see outcomes",
                        "Start a session in the Officer console; the funnel and charts fill in as you go.")
        else:
            section_header("Enrolment funnel", "Customer-product pairs from screening to approval.")
            st.altair_chart(funnel_chart(d["funnel"]), width="stretch")
            st.caption(funnel_sentence(d["funnel"]))

            v = st.session_state.get("dash_v", 0)  # bumping it resets every chart selection

            section_header("Eligibility by product", "Click a bar to filter the panels and the table below.")
            ev = st.altair_chart(product_status_chart(d["by_product"]), on_select="rerun",
                                 key=f"dash_prod_{v}", width="stretch")
            picked = (ev.selection.get("product") or [{}])[0].get("product_id") if ev else None
            st.session_state["dash_product"] = picked
            labels = {"rule": d["rule_labels"], "blocker": d["action_labels"], "doc": d["doc_labels"]}
            detail = st.session_state.get("dash_detail")

            active = [f"Product {picked}"] if picked else []
            if detail:
                active.append(labels[detail[0]].get(detail[1], detail[1]))
            if active:
                chip, clear = st.columns([5, 1], vertical_alignment="center")
                chip.markdown("**Filtered:** " + " · ".join(active))
                if clear.button("Clear filters", key="dash_clear", width="stretch", icon=":material/close:"):
                    st.session_state["dash_v"] = v + 1
                    st.session_state.pop("dash_detail", None)
                    st.session_state.pop("dash_product", None)
                    st.rerun()

            in_product = filter_results(d["customer_results"], picked)
            cols = st.columns(3, gap="medium")
            panels = [("Blocked by a prerequisite", "blocker", "No customer is waiting on a prerequisite."),
                      ("Most common not-eligible rules", "rule", "No rule has excluded anyone here."),
                      ("Most common missing documents", "doc", "No documents are missing here.")]
            for col, (title, kind, none_text) in zip(cols, panels, strict=True):
                with col:
                    st.markdown(f"**{title}**")
                    counts = panel_counts(in_product, kind, labels[kind])
                    # a new product filter can remove the selected bar: drop that filter instead of stranding it
                    if detail and detail[0] == kind and detail[1] not in {c["id"] for c in counts}:
                        st.session_state.pop("dash_detail", None)
                        st.rerun()
                    if not counts:
                        empty_state(none_text)
                        continue
                    pev = st.altair_chart(count_chart(counts, kind), on_select="rerun",
                                          key=f"dash_{kind}_{v}", width="stretch")
                    sel = (pev.selection.get(kind) or [{}])[0].get("id") if pev else None
                    # the panel whose selection just changed sets the detail filter
                    if sel != st.session_state.get(f"dash_last_{kind}"):
                        st.session_state[f"dash_last_{kind}"] = sel
                        if sel:
                            st.session_state["dash_detail"] = (kind, sel)
                        elif detail and detail[0] == kind:
                            st.session_state.pop("dash_detail", None)
                        st.rerun()

            section_header("Customers in this view", "Read-only: open a customer from the Officer console.")
            view = filter_results(d["customer_results"], picked, st.session_state.get("dash_detail"))
            if view:
                st.dataframe(result_table(view, d["rule_labels"], d["action_labels"], d["doc_labels"]),
                             width="stretch", hide_index=True, height=min(420, 38 * (len(view) + 1)),
                             column_config={"Queue": st.column_config.TextColumn(width="medium"),
                                            "Reason / blocker": st.column_config.TextColumn(width="large")})
            else:
                empty_state("No customer matches these filters", "Clear the filters to see everyone.")

        if d.get("sessions_by_state"):
            section_header("Sessions by stage")
            stage_chips(d["sessions_by_state"], {s: label(s) for s in d["sessions_by_state"]})

    section_header("Audit log", "Every agent, officer and system action, newest first. Stored redacted.")
    facets = audit_facets()
    f_session, f_actor, f_event, f_q = st.columns([2, 1, 2, 2], vertical_alignment="bottom")
    session_f = f_session.selectbox("Session", ["All", *facets.get("sessions", [])], key="audit_session")
    actor_names = {"agent": "Agent", "officer": "Officer", "system": "System"}
    actor_f = f_actor.selectbox("Actor", ["All", *facets.get("actors", [])], key="audit_actor",
                                format_func=lambda a: actor_names.get(a, a))
    events_f = f_event.multiselect("Event", facets.get("events", []), key="audit_events", format_func=field_label,
                                   placeholder="Any event")
    q_f = f_q.text_input("Search", key="audit_q", placeholder="e.g. approval_denied, Anjali, Q-…")
    params: dict = {"limit": 200}
    if session_f != "All":
        params["session_id"] = session_f
    if actor_f != "All":
        params["actor"] = actor_f
    if events_f:
        params["event"] = events_f
    if q_f.strip():
        params["q"] = q_f.strip()
    rows = api("GET", "/audit", params=params) or []
    if not rows:
        empty_state("No activity matches" if len(params) > 1 else "No activity yet",
                    "Actions are recorded here as soon as a session starts.")
    else:
        pick = st.dataframe(
            [{"Time": event_time(r["ts"]), "Session": r["session_id"] or "—",
              "Actor": actor_names.get(r["actor"], r["actor"]), "Event": field_label(r["event"]),
              "Details": readable(r["payload"], limit=200)} for r in rows],
            width="stretch", height=400, hide_index=True, on_select="rerun", selection_mode="single-row",
            key="audit_table")
        # facets are cached for 10 s and another client may have written since: never show "62 of 55"
        st.caption(f"Showing {len(rows)} of {max(facets.get('total', 0), len(rows))} events"
                   + (" (filtered)" if len(params) > 1 else "") + ". Select a row to see its full record.")
        chosen = pick.selection.rows if pick else []
        if chosen and chosen[0] < len(rows):
            r = rows[chosen[0]]
            with st.container(border=True, key="audit_record"):
                st.markdown(f"**{field_label(r['event'])}** · {event_datetime(r['ts'])} · "
                            f"{actor_names.get(r['actor'], r['actor'])}" + (f" · session {r['session_id']}"
                                                                             if r["session_id"] else ""))
                key_values(r["payload"] or {"details": None})
