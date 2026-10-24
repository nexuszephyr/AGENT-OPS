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
