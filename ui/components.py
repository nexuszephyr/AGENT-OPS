"""Shared UI components and design tokens for the Yojana Saathi officer UI (issue #16).

Every tab builds from these pieces, so the look stays consistent and later UI work
(#17 console, #19 queue, #21 dashboard) only changes layout, not style.

Rules:
- Status is never shown by colour alone: every badge is icon + text, and the six
  colours come from the Okabe-Ito colour-blind-safe set (text ≥ 4.5:1 on its tint).
- Anything that can come from the API, the LLM or a user is HTML-escaped before it
  is put into custom markup.
- The theme itself lives in .streamlit/config.toml; COLORS below mirrors it.
"""
from __future__ import annotations

import html
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import streamlit as st

# --- tokens -------------------------------------------------------------------------

COLORS = {
    "ink": "#18202C",        # body text
    "muted": "#5A6676",      # secondary text (5.5:1 on bg)
    "primary": "#1F4E8C",    # passbook ink blue
    "bg": "#F7F9FB",
    "surface": "#FFFFFF",
    "surface_alt": "#EAEFF5",
    "border": "#D5DDE8",
}


@dataclass(frozen=True)
class StatusStyle:
    label: str
    icon: str      # a text glyph, not an emoji: renders the same on every OS and projector
    fg: str        # text + border
    bg: str        # tint
    meaning: str   # one line for tooltips and the legend


# One palette for eligibility statuses, used everywhere (keys = app/rules/engine.py constants).
STATUS: dict[str, StatusStyle] = {
    "ELIGIBLE": StatusStyle("Eligible", "✓", "#00684A", "#E1F3EC",
                            "Meets every rule; a form can be prepared"),
    "ELIGIBLE_BLOCKED": StatusStyle("Eligible – blocked", "→", "#8A5300", "#FDF0D9",
                                    "Meets the rules but needs a prerequisite step first"),
    "NOT_ELIGIBLE": StatusStyle("Not eligible", "✕", "#454E5C", "#ECEEF2",
                                "Fails at least one rule; see the cited rules"),
    "UNKNOWN": StatusStyle("Unknown", "?", "#0B5A8F", "#E3EFF8",
                           "Missing information; the agent will ask"),
    "ALREADY_ENROLLED": StatusStyle("Already enrolled", "◆", "#7A3E66", "#F6E8F1",
                                    "The customer already holds this product"),
    "VERIFY_MANUALLY": StatusStyle("Verify manually", "!", "#A33F00", "#FBE6DA",
                                   "Could not be resolved by questions; the officer checks it"),
}

# Approval-queue statuses reuse the same tints, so "needs attention" always looks the same.
QUEUE_STATUS: dict[str, StatusStyle] = {
    "PENDING_APPROVAL": StatusStyle("Pending approval", "…", "#0B5A8F", "#E3EFF8", "Waiting for the officer"),
    "REJECTED": StatusStyle("Rejected", "✕", "#A33F00", "#FBE6DA", "The agent or officer revises it"),
    "NEEDS_OFFICER": StatusStyle("Needs officer", "!", "#A33F00", "#FBE6DA",
                                 "Revision limit reached; the officer edits it"),
    "REVISED": StatusStyle("Revised", "↻", "#8A5300", "#FDF0D9", "Back to the officer for approval"),
    "APPROVED": StatusStyle("Approved", "✓", "#00684A", "#E1F3EC", "Approved by the officer"),
    "SUBMITTED": StatusStyle("Submitted", "✓", "#00684A", "#E1F3EC", "Approved and submitted (mock)"),
}

_FALLBACK = StatusStyle("", "•", "#454E5C", "#ECEEF2", "")

_CSS = f"""
<style>
:root {{
  --ys-ink: {COLORS["ink"]}; --ys-muted: {COLORS["muted"]}; --ys-primary: {COLORS["primary"]};
  --ys-surface: {COLORS["surface"]}; --ys-surface-alt: {COLORS["surface_alt"]}; --ys-border: {COLORS["border"]};
}}
/* layout shell: one content width, tight top, works at 1366x768 and tablet widths */
[data-testid="stMainBlockContainer"], .block-container {{
  max-width: 1400px; padding-top: 2.25rem; padding-left: 1.5rem; padding-right: 1.5rem;
}}
header[data-testid="stHeader"] {{ background: transparent; }}
@media (max-width: 1024px) {{
  [data-testid="stMainBlockContainer"], .block-container {{ padding-left: 1rem; padding-right: 1rem; }}
}}
/* keyboard focus is always visible */
a:focus-visible, button:focus-visible, [role="tab"]:focus-visible {{
  outline: 3px solid var(--ys-primary); outline-offset: 2px;
}}

/* header */
.ys-header {{ display: flex; flex-wrap: wrap; gap: .75rem 1.5rem; align-items: flex-end;
  justify-content: space-between; padding-bottom: .9rem; margin-bottom: .25rem;
  border-bottom: 2px solid var(--ys-primary); }}
.ys-brand {{ display: flex; align-items: baseline; gap: .1rem .75rem; flex-wrap: wrap; max-width: 52rem; }}
.ys-name {{ font-size: 1.85rem; font-weight: 700; color: var(--ys-primary); line-height: 1.1; letter-spacing: -.01em; }}
.ys-name-kn {{ font-family: "Noto Sans Kannada", sans-serif; font-size: 1.15rem; font-weight: 600;
  color: var(--ys-muted); }}
.ys-tagline {{ flex-basis: 100%; color: var(--ys-muted); font-size: .92rem; }}
.ys-meta {{ display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; color: var(--ys-ink); font-size: .9rem; }}
.ys-meta-item {{ white-space: nowrap; }}
.ys-meta-item b {{ font-weight: 600; }}

/* pills (mode indicator) */
.ys-pill {{ display: inline-flex; align-items: center; gap: .4rem; padding: .2rem .7rem; border-radius: 999px;
  font-size: .85rem; font-weight: 600; border: 1.5px solid currentColor; white-space: nowrap; }}

/* status badge: the one bold element. A stamp: tinted, bordered, icon + text */
.ys-badge {{ display: inline-flex; align-items: center; gap: .4rem; padding: .12rem .55rem .12rem .2rem;
  border-radius: 4px; border: 1.5px solid currentColor; font-size: .85rem; font-weight: 600;
  line-height: 1.5; white-space: nowrap; vertical-align: middle; }}
.ys-badge-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 1.25rem; height: 1.25rem;
  border-radius: 3px; font-size: .8rem; font-weight: 700; color: #fff; }}

/* product card */
.ys-card-title.ys-card-stacked {{ flex-direction: column; align-items: flex-start; justify-content: flex-start;
  gap: .3rem; }}
.ys-card-title {{ display: flex; flex-wrap: wrap; gap: .35rem .75rem; align-items: center;
  justify-content: space-between; }}
.ys-card-name {{ font-weight: 600; font-size: 1.02rem; color: var(--ys-ink); }}
.ys-card-id {{ color: var(--ys-muted); font-size: .8rem; font-weight: 400; margin-left: .35rem; }}
.ys-card-benefit {{ color: var(--ys-muted); font-size: .9rem; margin: .25rem 0 0; }}

/* KPI grid: CSS grid, so tiles reflow on narrow screens without Streamlit columns */
.ys-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .75rem;
  margin: .25rem 0 1rem; }}
.ys-kpi {{ background: var(--ys-surface); border: 1px solid var(--ys-border); border-left: 4px solid var(--ys-primary);
  border-radius: 6px; padding: .7rem .9rem; }}
.ys-kpi-value {{ font-size: 1.75rem; font-weight: 700; line-height: 1.15; color: var(--ys-ink);
  font-variant-numeric: tabular-nums; }}
.ys-kpi-label {{ color: var(--ys-muted); font-size: .85rem; margin-top: .15rem; }}
.ys-kpi-attention {{ border-left-color: #A33F00; }}

/* section header */
.ys-section-caption {{ color: var(--ys-muted); font-size: .9rem; margin: -.6rem 0 .6rem; }}

/* empty state */
.ys-empty {{ border: 1.5px dashed var(--ys-border); border-radius: 8px; padding: 1.25rem 1rem; text-align: center;
  background: var(--ys-surface); }}
.ys-empty-title {{ font-weight: 600; color: var(--ys-ink); }}
.ys-empty-hint {{ color: var(--ys-muted); font-size: .9rem; margin-top: .25rem; }}

/* key/value list (form payloads) */
.ys-kv {{ display: grid; grid-template-columns: minmax(8rem, max-content) 1fr; gap: .2rem 1rem;
  font-size: .9rem; margin: .25rem 0; }}
.ys-kv dt {{ color: var(--ys-muted); }}
.ys-kv dd {{ margin: 0; color: var(--ys-ink); overflow-wrap: anywhere; }}

/* legend */
.ys-legend {{ display: flex; flex-wrap: wrap; gap: .4rem .6rem; margin: .25rem 0 .5rem; }}

/* customer picker + customer bar */
.ys-cust-name {{ font-weight: 600; font-size: 1.05rem; color: var(--ys-ink); }}
.ys-cust-meta {{ color: var(--ys-muted); font-size: .9rem; margin: .1rem 0 .35rem; }}
.ys-lang {{ display: inline-block; padding: .05rem .55rem; border-radius: 999px; background: var(--ys-surface-alt);
  color: var(--ys-ink); font-size: .8rem; font-weight: 600; }}
.ys-lang[lang="kn"] {{ font-family: "Noto Sans Kannada", sans-serif; }}
.ys-cbar {{ display: flex; flex-wrap: wrap; gap: .4rem .75rem; align-items: center; }}
.ys-cbar-name {{ font-weight: 700; font-size: 1.2rem; color: var(--ys-ink); }}
.ys-cbar-meta {{ color: var(--ys-muted); font-size: .95rem; }}
.ys-fact {{ display: inline-flex; gap: .3rem; align-items: center; padding: .08rem .5rem; border-radius: 4px;
  font-size: .82rem; font-weight: 600; border: 1px solid currentColor; white-space: nowrap; }}

/* product grid: columns wrap instead of squeezing on tablet widths */
.st-key-ys-product-grid [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
.st-key-ys-product-grid [data-testid="stColumn"] {{ min-width: min(230px, 100%); }}

/* a status that just changed: one pulse, then a steady outline (the "was" line says what changed) */
@keyframes ys-changed {{
  0%   {{ box-shadow: 0 0 0 0 rgba(31, 78, 140, .55); background-color: #E3EFF8; }}
  70%  {{ box-shadow: 0 0 0 10px rgba(31, 78, 140, 0); }}
  100% {{ box-shadow: 0 0 0 0 rgba(31, 78, 140, 0); background-color: transparent; }}
}}
[class*="st-key-ys-changed-"] {{ animation: ys-changed 1.4s ease-out 2; outline: 2px solid var(--ys-primary);
  outline-offset: -1px; border-radius: .5rem; }}
.ys-was {{ font-size: .82rem; color: var(--ys-primary); font-weight: 600; margin: .3rem 0 0; }}

/* the question the officer should ask now */
.ys-ask-label {{ font-size: .85rem; font-weight: 600; color: var(--ys-primary); }}
.ys-ask-text {{ font-size: 1.15rem; font-weight: 600; color: var(--ys-ink); margin: .1rem 0 .2rem; }}
.ys-ask-text[lang="kn"] {{ font-family: "Noto Sans Kannada", sans-serif; }}

/* text the agent wrote for the customer, in the customer's language */
.ys-to-customer {{ border-left: 4px solid #0B5A8F; background: #E3EFF8; border-radius: 6px; padding: .45rem .75rem;
  margin: .35rem 0; }}
.ys-to-customer-label {{ font-size: .8rem; font-weight: 600; color: #0B5A8F; }}
.ys-to-customer-text {{ font-size: 1.05rem; color: var(--ys-ink); }}
.ys-to-customer-text[lang="kn"] {{ font-family: "Noto Sans Kannada", sans-serif; }}

/* plan stepper: the steps are a real sequence, so they are numbered */
.ys-steps {{ list-style: none; margin: .25rem 0 .75rem; padding: 0; }}
.ys-step {{ display: grid; grid-template-columns: 2rem 1fr; gap: .7rem; position: relative; padding-bottom: .9rem; }}
.ys-step:not(:last-child)::before {{ content: ""; position: absolute; left: calc(1rem - 1px); top: 2.1rem;
  bottom: .1rem; width: 2px; background: var(--ys-border); }}
.ys-step-dot {{ width: 2rem; height: 2rem; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: .9rem; border: 2px solid currentColor;
  background: var(--ys-surface); font-variant-numeric: tabular-nums; }}
.ys-step-done .ys-step-dot {{ background: #00684A; border-color: #00684A; color: #fff !important; }}
.ys-step-blocked .ys-step-dot {{ border-style: dashed; }}
.ys-step-title {{ font-weight: 600; color: var(--ys-ink); line-height: 1.3; padding-top: .25rem; }}
.ys-step-done .ys-step-title {{ color: var(--ys-muted); }}
.ys-step-detail {{ color: var(--ys-muted); font-size: .85rem; }}
.ys-step-state {{ display: inline-block; font-size: .8rem; font-weight: 600; margin-top: .15rem; }}

@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


# --- page shell -----------------------------------------------------------------------

def setup_page(title: str = "Yojana Saathi") -> None:
    """Call once, first thing in the app: wide layout + the shared stylesheet."""
    st.set_page_config(page_title=title, page_icon="🏦", layout="wide", initial_sidebar_state="collapsed")
    _html(_CSS)


def mode_pill(llm_configured: bool, provider: str | None = None) -> str:
    if llm_configured:
        text = "Agent mode" + (f" · {provider}" if provider else "")
        return (f'<span class="ys-pill" style="color:#00684A;background:#E1F3EC" '
                f'title="The LLM runs the conversation; the rules engine still decides eligibility">'
                f'<span aria-hidden="true">●</span>{_e(text)}</span>')
    return ('<span class="ys-pill" style="color:#8A5300;background:#FDF0D9" '
            'title="No LLM: statuses, plan and questions come from the rules engine. '
            'Answer questions with the form under the chat, then use Prepare all steps.">'
            '<span aria-hidden="true">≡</span>Rules-only mode</span>')


def app_header(*, llm_configured: bool, provider: str | None, officer: str, branch: str) -> None:
    """Name, tagline, mode pill, officer and branch. Wraps cleanly on tablet widths."""
    _html(
        '<header class="ys-header" role="banner">'
        '<div class="ys-brand">'
        '<span class="ys-name">Yojana Saathi</span>'
        '<span class="ys-name-kn" lang="kn">ಯೋಜನಾ ಸಾಥಿ</span>'
        '<span class="ys-tagline">Scheme screening and enrolment for bank branches. '
        'Nothing is submitted without your approval.</span>'
        '</div>'
        '<div class="ys-meta">'
        f'{mode_pill(llm_configured, provider)}'
        f'<span class="ys-meta-item">Officer <b>{_e(officer)}</b></span>'
        f'<span class="ys-meta-item">Branch <b>{_e(branch)}</b></span>'
        '</div></header>'
    )


def section_header(title: str, caption: str | None = None) -> None:
    """Native subheader (keeps heading semantics and the smoke test), plus an optional caption."""
    st.subheader(title, anchor=False)
    if caption:
        _html(f'<p class="ys-section-caption">{_e(caption)}</p>')


def empty_state(title: str, hint: str = "") -> None:
    """An empty area says what to do next, not just that it is empty."""
    _html(f'<div class="ys-empty" role="status"><div class="ys-empty-title">{_e(title)}</div>'
          + (f'<div class="ys-empty-hint">{_e(hint)}</div>' if hint else "") + "</div>")


# --- status ---------------------------------------------------------------------------

def status_style(status: str, palette: Mapping[str, StatusStyle] = STATUS) -> StatusStyle:
    s = palette.get(status)
    if s:
        return s
    return StatusStyle(status.replace("_", " ").capitalize(), _FALLBACK.icon, _FALLBACK.fg, _FALLBACK.bg, "")


def status_badge(status: str, palette: Mapping[str, StatusStyle] = STATUS) -> str:
    """HTML for one badge: icon + text, never colour alone."""
    s = status_style(status, palette)
    return (f'<span class="ys-badge" style="color:{s.fg};background:{s.bg}" title="{_e(s.meaning)}">'
            f'<span class="ys-badge-icon" style="background:{s.fg}" aria-hidden="true">{_e(s.icon)}</span>'
            f'{_e(s.label)}</span>')


def queue_badge(status: str) -> str:
    return status_badge(status, QUEUE_STATUS)


def status_legend() -> None:
    _html('<div class="ys-legend" aria-label="Status legend">'
          + "".join(status_badge(k) for k in STATUS) + "</div>")


# --- cards and tiles ------------------------------------------------------------------

def card_title(name: str, meta: str = "", badge_html: str = "", detail: str = "", stacked: bool = False) -> None:
    """Title row shared by every card: name, small meta text, a badge, optional detail line.

    Default: badge on the right. `stacked=True` (grid cards): badge first, then the name, so badges
    line up across a row whatever the name length.
    `badge_html` must come from status_badge / queue_badge; everything else is escaped here.
    """
    name_html = (f'<span class="ys-card-name">{_e(name)}'
                 + (f'<span class="ys-card-id">{_e(meta)}</span>' if meta else "") + "</span>")
    inner = f"{badge_html}{name_html}" if stacked else f"{name_html}{badge_html}"
    cls = "ys-card-title ys-card-stacked" if stacked else "ys-card-title"
    _html(f'<div class="{cls}">{inner}</div>'
          + (f'<p class="ys-card-benefit">{_e(detail)}</p>' if detail else ""))


def product_card(result: Mapping[str, Any], previous_status: str | None = None,
                 action_labels: Mapping[str, str] | None = None) -> None:
    """One product: name, badge, benefit, and an expandable 'why' with the cited rules.

    `previous_status` (when it differs) highlights the card once and says what it was before,
    so the change is visible without relying on the animation.
    """
    pid = result["product_id"]
    changed = previous_status is not None and previous_status != result["status"]
    labels = action_labels or {}
    with st.container(border=True, key=f"ys-changed-{pid}" if changed else f"ys-card-{pid}"):
        card_title(result.get("name") or pid, pid, status_badge(result["status"]), result.get("benefit") or "",
                   stacked=True)
        if changed:
            _html(f'<p class="ys-was" role="status">Changed from {_e(status_style(previous_status or "").label)}'
                  f' to {_e(status_style(result["status"]).label)}</p>')
        failed = result.get("failed_rules") or []
        blocked = result.get("blocked_by") or []
        unknown = result.get("unknown_fields") or []
        with st.expander("Why this status"):
            for f in failed:
                st.markdown(f"✕ {f['text']}  `{f['id']}`")
            for a in blocked:
                st.markdown(f"→ Needs **{labels.get(a, a)}** first")
            for u in unknown:
                st.markdown(f"? Missing: {u.replace('_', ' ')}")
            if not (failed or blocked or unknown):
                st.markdown(status_style(result["status"]).meaning + ".")
            src = result.get("source_url")
            verified = result.get("last_verified") or "not yet verified"
            if src:
                st.caption(f"[Scheme source]({src}) · rules last verified: {verified}")


def kpi_tiles(tiles: Iterable[tuple[str, Any] | tuple[str, Any, bool]]) -> None:
    """A responsive row of KPI tiles. Each tile is (label, value) or (label, value, needs_attention)."""
    cells = []
    for t in tiles:
        label, value = t[0], t[1]
        attention = bool(t[2]) if len(t) > 2 else False
        cls = "ys-kpi ys-kpi-attention" if attention else "ys-kpi"
        cells.append(f'<div class="{cls}"><div class="ys-kpi-value">{_e(value)}</div>'
                     f'<div class="ys-kpi-label">{_e(label)}</div></div>')
    _html('<div class="ys-kpis">' + "".join(cells) + "</div>")


def key_values(data: Mapping[str, Any], hide: Sequence[str] = ()) -> None:
    """A readable field list instead of raw JSON (payloads, profiles)."""
    rows = "".join(f"<dt>{_e(k.replace('_', ' ').capitalize())}</dt><dd>{_e('—' if v in (None, '') else v)}</dd>"
                   for k, v in data.items() if k not in hide)
    _html(f'<dl class="ys-kv">{rows}</dl>')


# --- officer console (issue #17) --------------------------------------------------------

LANGUAGES: dict[str, tuple[str, str]] = {  # code -> (native name, English name)
    "kn": ("ಕನ್ನಡ", "Kannada"),
    "en": ("English", "English"),
    "hi": ("हिन्दी", "Hindi"),
}


def language_chip(code: str | None) -> str:
    native, english = LANGUAGES.get(code or "en", (code or "", code or ""))
    return f'<span class="ys-lang" lang="{_e(code or "en")}" title="Prefers {_e(english)}">{_e(native)}</span>'


def customer_card(c: Mapping[str, Any]) -> bool:
    """One customer in the picker. Returns True when the officer starts a session for them."""
    with st.container(border=True):
        occupation = str(c.get("occupation") or "").replace("_", " ")
        _html(f'<div class="ys-cust-name">{_e(c.get("name"))}</div>'
              f'<div class="ys-cust-meta">{_e(c.get("age"))} · {_e(occupation)} · {_e(c.get("district"))}</div>'
              f'{language_chip(c.get("preferred_language"))}')
        return st.button("Start session", key=f"start_{c['customer_id']}", width="stretch")


def _fact(ok: bool | None, yes: str, no: str, unknown: str) -> str:
    if ok is None:
        return f'<span class="ys-fact" style="color:#0B5A8F;background:#E3EFF8">? {_e(unknown)}</span>'
    if ok:
        return f'<span class="ys-fact" style="color:#00684A;background:#E1F3EC">✓ {_e(yes)}</span>'
    return f'<span class="ys-fact" style="color:#A33F00;background:#FBE6DA">✕ {_e(no)}</span>'


def customer_bar(profile: Mapping[str, Any]) -> None:
    """Who is in front of the officer, and the account facts that block or unlock schemes."""
    occupation = str(profile.get("occupation") or "").replace("_", " ")
    account = profile.get("account_no_masked")
    _html('<div class="ys-cbar">'
          f'<span class="ys-cbar-name">{_e(profile.get("name"))}</span>'
          f'<span class="ys-cbar-meta">{_e(profile.get("age"))} · {_e(occupation)} · {_e(profile.get("district"))}'
          '</span>'
          f'{language_chip(profile.get("preferred_language"))}'
          + _fact(profile.get("has_savings_account"), f"Account {account}" if account else "Savings account",
                  "No savings account", "Account not known")
          + _fact(profile.get("aadhaar_seeded"), "Aadhaar seeded", "Aadhaar not seeded", "Seeding not known")
          + _fact(profile.get("ekyc_done"), "eKYC done", "eKYC pending", "eKYC not known")
          + "</div>")


def product_grid(results: Sequence[Mapping[str, Any]], previous: Mapping[str, str] | None = None,
                 action_labels: Mapping[str, str] | None = None, columns: int = 2) -> None:
    """Every product as a card, in rows of `columns`; cards wrap on narrow screens."""
    previous = previous or {}
    with st.container(key="ys-product-grid"):
        for start in range(0, len(results), columns):
            cols = st.columns(columns, gap="small")
            for col, r in zip(cols, results[start:start + columns], strict=False):
                with col:
                    product_card(r, previous.get(r["product_id"]), action_labels)


def question_prompt(text_en: str, text_kn: str | None = None) -> None:
    """The question to ask now, in English and (when known) in Kannada."""
    _html('<div class="ys-ask-label">Ask the customer</div>'
          f'<div class="ys-ask-text">{_e(text_en)}</div>'
          + (f'<div class="ys-ask-text" lang="kn">{_e(text_kn)}</div>' if text_kn else ""))


def customer_bubble(lang: str | None, text: str) -> None:
    """Text the agent wrote for the customer, set apart from what it says to the officer."""
    native, english = LANGUAGES.get(lang or "en", (lang or "", lang or ""))
    _html('<div class="ys-to-customer">'
          f'<div class="ys-to-customer-label">Say to the customer ({_e(english)})</div>'
          f'<div class="ys-to-customer-text" lang="{_e(lang or "en")}">{_e(text)}</div></div>')


@dataclass(frozen=True)
class Step:
    title: str
    state: str          # key of STEP_STATES
    detail: str = ""


# How a plan step looks: (dot colour, status text). Done steps show ✓ instead of their number.
STEP_STATES: dict[str, tuple[str, str]] = {
    "done": ("#00684A", "✓ Approved by you"),
    "pending": ("#0B5A8F", "… Waiting for your approval"),
    "rejected": ("#A33F00", "✕ Rejected: being revised"),
    "needs_officer": ("#A33F00", "! Needs you: revision limit reached"),
    "todo": ("#1F4E8C", "Ready to prepare"),
    "blocked": ("#5A6676", "→ Waiting on an earlier step"),
}


def stepper(steps: Sequence[Step]) -> None:
    """Numbered plan with done ticks. Each step shows its state as icon + text, not colour alone."""
    rows = []
    for n, s in enumerate(steps, 1):
        colour, state_text = STEP_STATES.get(s.state, ("#5A6676", s.state))
        dot = "✓" if s.state == "done" else str(n)
        label = f"Step {n}, {state_text.lstrip('✓…✕!→ ')}"
        rows.append(f'<li class="ys-step ys-step-{_e(s.state)}" aria-label="{_e(label)}">'
                    f'<span class="ys-step-dot" style="color:{colour}" aria-hidden="true">{_e(dot)}</span>'
                    f'<div><div class="ys-step-title">{_e(s.title)}</div>'
                    + (f'<div class="ys-step-detail">{_e(s.detail)}</div>' if s.detail else "")
                    + f'<span class="ys-step-state" style="color:{colour}">{_e(state_text)}</span></div></li>')
    _html('<ol class="ys-steps">' + "".join(rows) + "</ol>")


# Queue status -> stepper state
ITEM_STEP_STATE: dict[str, str] = {"SUBMITTED": "done", "APPROVED": "done", "PENDING_APPROVAL": "pending",
                                   "REVISED": "pending", "REJECTED": "rejected", "NEEDS_OFFICER": "needs_officer"}


def _queued_item(items: Sequence[Mapping[str, Any]], step: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The queue item covering a plan step (same matching as app/sessions.py)."""
    for i in items:
        if step.get("product_id") and i.get("product_id") == step["product_id"]:
            return i
        if step.get("type") == "SERVICE_REQUEST" and i.get("action_id") == step.get("action_id"):
            return i
    return None


def plan_steps(state: Mapping[str, Any], action_labels: Mapping[str, str]) -> list[Step]:
    """Approved steps first (ticked), then the remaining plan in order, each with its queue state.

    Approved steps drop out of the planner's output (their effect is now in the customer's
    profile), so they are taken from the queue instead. Pure function: no Streamlit calls.
    """
    names = {r["product_id"]: r["name"] for r in state["results"]}

    def title(product_id: str | None, action_id: str | None) -> str:
        if action_id and action_id in action_labels:
            return action_labels[action_id]
        return f"Enrol in {names.get(product_id or '', product_id)}"

    items = state["queue_items"]
    done = sorted((i for i in items if ITEM_STEP_STATE.get(i["status"]) == "done"),
                  key=lambda i: i.get("decided_at") or "")
    steps = [Step(title(i.get("product_id"), i.get("action_id")), "done") for i in done]
    for s in state["plan"]["steps"]:
        item = _queued_item(items, s)
        if item and ITEM_STEP_STATE.get(item["status"]) == "done":
            continue
        detail = "Unlocks " + ", ".join(names.get(p, p) for p in s["unlocks"]) if s.get("unlocks") else ""
        if item:
            step_state = ITEM_STEP_STATE.get(item["status"], "pending")
        elif s.get("after"):
            step_state = "blocked"
            detail = "After: " + ", ".join(action_labels.get(a, a) for a in s["after"])
        else:
            step_state = "todo"
        steps.append(Step(title(s.get("product_id"), s.get("action_id")), step_state, detail))
    return steps
