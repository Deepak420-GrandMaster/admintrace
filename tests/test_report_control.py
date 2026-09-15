"""The floating report control: one circle, and nothing else.

These are contract tests over the stylesheet and the page script, not a
substitute for looking at it — the defect they exist to prevent was visible
in a screenshot and invisible in the DOM tree until the computed styles were
read. What they do catch is the shape of the mistake: a selector broad enough
to style Gradio's own furniture, or a label that reserves layout.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.ui.app import HEAD
from app.ui.i18n import t

STYLES = (Path(__file__).resolve().parent.parent
          / "app" / "ui" / "styles.css").read_text(encoding="utf-8")

# Comments carry no braces, so a naive rule split glues each one onto the
# selector that follows it.
_RULES = re.sub(r"/\*.*?\*/", "", STYLES, flags=re.S)


def _rule(selector_fragment: str) -> str:
    """The declaration body of the first rule whose selector contains this."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _RULES):
        if selector_fragment in match.group(1):
            return match.group(2)
    return ""


def _exact_rule(selector: str) -> str:
    """The declaration body of the rule with exactly this selector."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _RULES):
        if match.group(1).strip() == selector:
            return match.group(2)
    return ""


# ------------------------------------------------------------- the shape --

def test_the_control_is_a_circle_of_a_touch_sized_44px():
    body = _rule(".rp-report > .label-wrap,")
    assert "border-radius: 50%" in body
    assert "width: 44px" in body and "height: 44px" in body
    # Capped, so no inherited Gradio sizing can stretch it.
    assert "max-width: 44px" in body and "max-height: 44px" in body


def test_the_control_reserves_no_space_beyond_the_circle():
    """The wrapper stretched to 61px when the hidden panel kept a margin."""
    body = _exact_rule(".gradio-container .rp-report")
    assert "width: 44px" in body and "height: 44px" in body


def test_the_panel_is_out_of_flow_so_opening_it_moves_nothing():
    body = _rule(".rp-report > :not(.label-wrap):not(.wrap)")
    assert "position: absolute" in body


def test_gradio_status_element_is_removed_from_this_control():
    """This element, styled by accident, was the grey bar under the button."""
    assert ".rp-report > .wrap { display: none !important; }" in STYLES


def test_the_panel_selector_excludes_the_status_element():
    """The root cause: ':not(.label-wrap)' alone also matched Gradio's .wrap."""
    for match in re.finditer(r"\.rp-report > :not\(\.label-wrap\)([^\s{]*)", STYLES):
        assert ":not(.wrap)" in match.group(0), (
            "a rule still styles every non-header child of the report control, "
            "which is what put a bordered slab under the button")


# -------------------------------------------------------------- the icon --

def test_the_glyph_is_an_svg_mask_not_an_emoji_or_a_character():
    body = _rule(".rp-report > .label-wrap::before")
    assert "mask:" in body
    assert "var(--rp-bug)" in body
    # Coloured from the element, so it follows the token rather than being
    # baked into the asset.
    assert "background-color: currentColor" in body


def test_the_glyph_is_declared_once_as_a_token():
    assert "--rp-bug:" in STYLES
    assert "svg+xml" in STYLES.split("--rp-bug:")[1][:200]


def test_the_glyph_fits_inside_the_circle():
    body = _rule(".rp-report > .label-wrap::before")
    size = re.search(r"width: (\d+)px", body)
    assert size and 16 <= int(size.group(1)) <= 22


# ------------------------------------------------------- the label -------

def test_the_label_is_clipped_rather_than_removed():
    """It has to announce to a screen reader and occupy no layout."""
    body = _rule(".rp-report > .label-wrap > span:not(.icon)")
    assert "position: absolute" in body
    assert "clip-path: inset(50%)" in body or "clip: rect(0 0 0 0)" in body
    # Neither of these hides an element from assistive technology, and both
    # would leave it taking up room.
    assert "visibility: hidden" not in body
    assert "opacity: 0" not in body


def test_no_visible_text_is_painted_in_the_control():
    body = _rule(".rp-report > .label-wrap,")
    assert "font-size: 0" in body


# ----------------------------------------------------------- the tooltip --

def test_the_tooltip_is_absent_until_hover_or_focus():
    resting = _rule(".rp-report > .label-wrap::after")
    assert "opacity: 0" in resting
    assert "pointer-events: none" in resting
    shown = _rule(".rp-report > .label-wrap:hover::after")
    assert "opacity: 1" in shown
    assert ":focus-visible::after" in STYLES


def test_the_accessible_name_is_supplied_in_both_languages():
    assert t("en", "report_open") == "Report a problem"
    assert t("fr", "report_open") == "Signaler un problème"
    assert t("en", "report_open") in HEAD
    assert t("fr", "report_open") in HEAD


def test_the_page_sets_the_accessible_name_and_the_tooltip_together():
    assert 'setAttribute("aria-label", text)' in HEAD
    assert 'setAttribute("data-tip", text)' in HEAD


# ------------------------------------------------------------- scoping ---

def test_the_control_never_restyles_buttons_generally():
    """A broad button rule here would repaint the whole application."""
    for match in re.finditer(r"^\s*(button|\.gr-button)\s*\{", STYLES, re.M):
        raise AssertionError(f"unscoped button rule: {match.group(0).strip()}")


def test_the_circle_is_the_only_thing_shaped_like_one():
    """Nothing else in the stylesheet should be a 50% radius control."""
    owners = [selector.strip() for selector, body
              in re.findall(r"([^{}]+)\{([^{}]*)\}", _RULES)
              if "border-radius: 50%" in body]
    for owner in owners:
        assert "rp-report" in owner or "rp-eyebrow" in owner \
            or "rp-think-dots" in owner or "rp-badge" in owner \
            or "rp-stage-dot" in owner, f"unexpected circle: {owner}"


# ------------------------------------------------------------ position ---

def test_the_control_sits_clear_of_the_mobile_safe_area():
    body = _exact_rule(".gradio-container .rp-report")
    assert "env(safe-area-inset-bottom" in body


def test_motion_is_dropped_when_it_is_not_wanted_but_the_tooltip_is_not():
    reduced = STYLES.split("prefers-reduced-motion")[1]
    assert ".rp-report > .label-wrap:hover" in reduced
    assert "transform: none" in reduced
