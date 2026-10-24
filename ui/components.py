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
  max-width: 1400px; padding-top: 3.25rem; padding-left: 1.5rem; padding-right: 1.5rem;
}}
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
.ys-brand {{ display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }}
.ys-name {{ font-size: 1.85rem; font-weight: 700; color: var(--ys-primary); line-height: 1.1; letter-spacing: -.01em; }}
.ys-name-kn {{ font-family: "Noto Sans Kannada", sans-serif; font-size: 1.15rem; font-weight: 600;
  color: var(--ys-muted); }}
.ys-tagline {{ flex-basis: 100%; color: var(--ys-muted); font-size: .95rem; margin-top: .15rem; }}
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
        '<span class="ys-tagline">Screen customers for government schemes, plan the paperwork, '
        'and prepare enrolments. Nothing is submitted without your approval.</span>'
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

def card_title(name: str, meta: str = "", badge_html: str = "", detail: str = "") -> None:
    """Title row shared by every card: name, small meta text, a badge on the right, optional detail line.

    `badge_html` must come from status_badge / queue_badge; everything else is escaped here.
    """
    _html('<div class="ys-card-title">'
          f'<span class="ys-card-name">{_e(name)}'
          + (f'<span class="ys-card-id">{_e(meta)}</span>' if meta else "")
          + f'</span>{badge_html}</div>'
          + (f'<p class="ys-card-benefit">{_e(detail)}</p>' if detail else ""))


def product_card(result: Mapping[str, Any]) -> None:
    """One product: name, badge, benefit, and an expandable 'why' with the cited rules."""
    with st.container(border=True):
        card_title(result.get("name") or result["product_id"], result["product_id"],
                   status_badge(result["status"]), result.get("benefit") or "")
        failed = result.get("failed_rules") or []
        blocked = result.get("blocked_by") or []
        unknown = result.get("unknown_fields") or []
        with st.expander("Why this status"):
            for f in failed:
                st.markdown(f"✕ {f['text']}  `{f['id']}`")
            for a in blocked:
                st.markdown(f"→ Needs **{a}** first")
            for u in unknown:
                st.markdown(f"? Missing `{u}`")
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
