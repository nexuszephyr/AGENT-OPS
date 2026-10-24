"""UI design system (issue #16): the palette can't drift from the engine, and it stays accessible."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from app import queue
from app.rules import engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

import components as ui  # noqa: E402


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_engine_status_has_exactly_one_style():
    assert set(ui.STATUS) == set(engine.ALL_STATUSES)


def test_every_visible_queue_status_has_a_style():
    visible = set(queue.TRANSITIONS) - {queue.DRAFT}  # DRAFT never reaches the UI
    assert visible <= set(ui.QUEUE_STATUS)


@pytest.mark.parametrize("palette", [ui.STATUS, ui.QUEUE_STATUS], ids=["eligibility", "queue"])
def test_status_is_never_colour_alone(palette):
    for key, s in palette.items():
        assert s.label and s.icon, key
        assert contrast(s.fg, s.bg) >= 4.5, f"{key}: text on tint below WCAG AA"
        assert contrast("#FFFFFF", s.fg) >= 4.5, f"{key}: icon on its chip below WCAG AA"


def test_eligibility_statuses_are_distinguishable_without_colour():
    icons = [s.icon for s in ui.STATUS.values()]
    labels = [s.label for s in ui.STATUS.values()]
    assert len(set(icons)) == len(icons)
    assert len(set(labels)) == len(labels)


def test_badge_shows_icon_and_text():
    html = ui.status_badge(engine.ELIGIBLE_BLOCKED)
    assert "Eligible – blocked" in html and "→" in html


def test_unknown_status_still_renders_readably():
    assert "Some new status" in ui.status_badge("SOME_NEW_STATUS")


def test_custom_markup_escapes_untrusted_text(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(ui, "_html", out.append)
    ui.card_title("<script>x</script>", meta="<b>m</b>", detail="<img src=x onerror=alert(1)>")
    ui.empty_state("<i>t</i>", "<u>h</u>")
    ui.kpi_tiles([("<x>", "<y>")])
    ui.key_values({"nominee_name": "<script>alert(1)</script>"})
    ui.app_header(llm_configured=True, provider="<p>", officer="<o>", branch="<br>")
    joined = "".join(out)
    for raw in ("<script>", "<b>m", "<img", "<i>t", "<u>h", "<x>", "<y>", "<p>", "<o>", "<br>"):
        assert raw not in joined, raw


def test_theme_matches_tokens_and_bundled_fonts_exist():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = cfg["theme"]
    assert theme["primaryColor"].upper() == ui.COLORS["primary"].upper()
    assert theme["textColor"].upper() == ui.COLORS["ink"].upper()
    assert cfg["server"]["enableStaticServing"] is True
    assert contrast(theme["textColor"], theme["backgroundColor"]) >= 7
    for face in theme["fontFaces"]:
        rel = face["url"].removeprefix("app/static/")
        assert (ROOT / "ui" / "static" / rel).is_file(), face["url"]


def test_one_palette_no_raw_hex_outside_the_token_tables():
    """PR #40 review: colours outside STATUS/QUEUE_STATUS/COLORS must reference them, so the contrast tests
    above cover every colour the UI uses."""
    import re

    src = (ROOT / "ui" / "components.py").read_text(encoding="utf-8")
    after_tokens = src.split("\n_FALLBACK = ", 1)[1]
    assert re.findall(r"#[0-9A-Fa-f]{6}\b", after_tokens) == []


def test_tones_are_status_entries():
    for name, tone in ui.TONE.items():
        assert tone in ui.STATUS.values(), name


def test_plan_step_colours_are_readable():
    for key, (colour, _text) in ui.STEP_STATES.items():
        for background in (ui.COLORS["bg"], ui.COLORS["surface"]):
            assert contrast(colour, background) >= 4.5, f"{key} on {background}"


# --- branch dashboard (issue #21) ---------------------------------------------------------

def test_dashboard_chips_and_table_escape_names_and_rule_text():
    html = ui.state_chips({"<b>ASKING</b>": 1, "ESCALATED": 2}, {"<b>ASKING</b>": "<script>x</script>"})
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert ui.TONE["attention"].fg in html  # an escalation is highlighted with the palette colour
    rows = ui.result_table(
        [{"customer_id": "C9", "customer_name": "<img src=x>", "product_id": "APY", "status": "NOT_ELIGIBLE",
          "failed_rules": ["R1"], "blocked_by": [], "missing_docs": [], "queue_status": None, "value_inr": None}],
        {"R1": "Age must be 40 or below"}, {}, {})
    assert rows[0]["Status"] == "Not eligible" and rows[0]["Reason / blocker"] == "Age must be 40 or below"
    assert rows[0]["₹ value (indicative)"] == "—" and rows[0]["Queue"] == "—"  # null is never ₹0
    # dataframe cells are plain text, not HTML: the name is passed through for Streamlit to render as text
    assert rows[0]["Customer"].startswith("<img")


def test_dashboard_status_colours_come_from_the_palette():
    scale = ui.status_scale().to_dict()
    assert scale["domain"] == [s.label for s in ui.STATUS.values()]
    assert scale["range"] == [s.fg for s in ui.STATUS.values()]
    spec = ui.product_status_chart({"APY": {"name": "APY", "statuses": {"NOT_ELIGIBLE": 2, "ELIGIBLE": 1}}}).to_dict()
    assert spec["encoding"]["color"]["scale"]["range"] == [s.fg for s in ui.STATUS.values()]


@pytest.mark.parametrize("minutes, text", [(None, "—"), (0.2, "<1 min"), (4, "4 min"), (60, "1 h"), (80, "1 h 20 min")])
def test_format_minutes(minutes, text):
    assert ui.format_minutes(minutes) == text


def test_funnel_rows_and_sentence():
    funnel = [{"stage": "screened", "count": 2}, {"stage": "eligible", "count": 8, "value_inr": 818000},
              {"stage": "queued", "count": 7}, {"stage": "approved", "count": 7, "value_inr": 812000}]
    rows = ui.funnel_rows(funnel)
    assert [r["text"] for r in rows] == ["2", "8 · ₹8.2 lakh", "7 · 88%", "7 · 100% · ₹8.1 lakh"]
    assert ui.funnel_sentence(funnel) == "8 eligible products → 7 queued → 7 approved (₹8.1 lakh unlocked, indicative)"
    assert ui.funnel_sentence([{"stage": "screened", "count": 0}]) == "No customer has been screened yet."
    ui.funnel_chart(funnel).to_dict()  # builds without error, also on empty data
    ui.funnel_chart([{"stage": "screened", "count": 0}]).to_dict()
    ui.count_chart([], "rule").to_dict()


def test_dashboard_filters_and_panel_counts():
    rows = [{"customer_id": "C1", "product_id": "APY", "failed_rules": ["R1"], "blocked_by": [], "missing_docs": []},
            {"customer_id": "C2", "product_id": "APY", "failed_rules": ["R1", "R2"], "blocked_by": [],
             "missing_docs": []},
            {"customer_id": "C1", "product_id": "PMJJBY", "failed_rules": [], "blocked_by": ["SEED"],
             "missing_docs": ["NOMINEE"]},
            {"customer_id": "C1", "product_id": "PMSBY", "failed_rules": [], "blocked_by": [],
             "missing_docs": ["NOMINEE"]}]
    assert len(ui.filter_results(rows, "APY")) == 2
    assert [r["customer_id"] for r in ui.filter_results(rows, "APY", ("rule", "R2"))] == ["C2"]
    assert ui.panel_counts(rows, "rule", {"R1": "Rule one"})[0] == {"id": "R1", "label": "Rule one", "count": 2}
    assert ui.panel_counts(rows, "doc", {}) == [{"id": "NOMINEE", "label": "NOMINEE", "count": 1}]  # once per customer
    assert ui.panel_counts(ui.filter_results(rows, "APY"), "blocker", {}) == []
