"""The two ways this interface has broken silently.

Both failures here were real, and neither showed up as an exception: the page
rendered, the server logged nothing, and a chunk of the interface simply did
not work. They are cheap to assert and expensive to find by hand.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.ui import brand, render
from app.ui.app import HEAD
from app.ui.i18n import CATEGORIES, STRINGS, t

STYLES = (Path(__file__).resolve().parent.parent
          / "app" / "ui" / "styles.css").read_text(encoding="utf-8")


# ------------------------------------------------------- the page script --

def test_the_injected_script_parses():
    """A syntax error here takes out every behaviour on the page at once.

    It happened: the template was not a raw string, Python turned the ``\\n``
    inside a JavaScript string literal into a real newline, and the whole
    script failed to parse. Nothing else in the app noticed.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available to parse the script")
    script = re.search(r"<script>(.*)</script>", HEAD, re.S)
    assert script, "the page script is missing from HEAD"

    result = subprocess.run(
        [node, "--input-type=module", "--check"],
        input=script.group(1), text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, f"page script does not parse:\n{result.stderr}"


def test_javascript_escapes_survive_into_the_page():
    r"""``\n`` in the template must reach the browser as an escape, not a newline."""
    assert "\\n" in HEAD


def test_every_placeholder_in_the_template_was_filled():
    assert not re.search(r"__[A-Z_]+__", HEAD), "an unreplaced token reached the page"


def test_both_languages_reach_the_page_script():
    """The script picks its own wording, so it needs both languages in it."""
    for key in ("skip", "report_open"):
        assert t("en", key) in HEAD
        assert t("fr", key) in HEAD


# ------------------------------------------------------------ the styles --

def test_no_media_query_rule_is_scoped_out_of_existence():
    """Gradio rewrites our rules; one shape of selector cannot survive it.

    Every rule is re-emitted as ``.gradio-container … .contain <selector>``.
    Inside a media query that rewritten copy is the only one, so a selector
    written as ``.gradio-container .rp-thing`` becomes
    ``… .contain .gradio-container .rp-thing`` and matches nothing — there is
    no second container inside ``.contain``. Silently dead, which is how the
    16px phone font size on the question field was lost.
    """
    offenders: list[str] = []
    depth = 0
    in_media = False
    for number, line in enumerate(STYLES.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("/*") or stripped.startswith("*"):
            continue
        if stripped.startswith("@media"):
            in_media, depth = True, 0
        if in_media:
            depth += line.count("{") - line.count("}")
            if stripped.startswith(".gradio-container"):
                offenders.append(f"styles.css:{number}: {stripped}")
            if depth <= 0 and "}" in line and not stripped.startswith("@media"):
                in_media = False
    assert not offenders, (
        "these rules are scoped out of existence inside a media query:\n"
        + "\n".join(offenders))


def test_every_class_the_python_emits_is_either_styled_or_an_element_id():
    """A class that nothing styles is usually a rename that was half-finished."""
    # elem_id values and hooks read only by the page script.
    exempt = {"rp-question", "rp-browser", "rp-viewport", "rp-theme",
              "rp-masthead", "rp-reply", "rp-mark-dense"}
    emitted = set()
    for path in (Path("app/ui/render.py"), Path("app/ui/app.py"),
                 Path("app/ui/brand.py")):
        emitted |= set(re.findall(r"rp-[a-z0-9-]+",
                                  path.read_text(encoding="utf-8")))
    styled = set(re.findall(r"\.(rp-[a-z0-9-]+)", STYLES))
    # Built at runtime as rp-cat-{slug}.
    styled |= {f"rp-cat-{slug}" for _, slug, _ in CATEGORIES["en"]}
    missing = sorted(c for c in emitted - styled - exempt if not c.endswith("-"))
    assert not missing, f"emitted but never styled: {missing}"


# ------------------------------------------------------------- the mark --

def test_the_mark_and_the_favicon_stay_in_step():
    """The favicon is the mark flattened; a change to one needs the other."""
    assert "M3 18.9h16.5" in brand.mark()
    assert "M3 18.9h16.5" in brand.FAVICON


def test_the_wordmark_keeps_its_accent():
    assert "é" in brand.wordmark()


# ------------------------------------------------- topics and the answer --

def test_every_topic_row_has_a_label_and_a_description():
    for language in ("en", "fr"):
        markup = render.categories_html(language)
        for key, _slug, _question in CATEGORIES[language]:
            assert html.escape(t(language, key)) in markup
            assert html.escape(STRINGS[language][f"{key}_sub"]) in markup


def test_the_answer_is_not_wrapped_in_a_card():
    """The grey box around every answer was the single worst thing on screen."""
    match = re.search(r"^\.rp-answer \{([^}]*)\}", STYLES, re.M)
    assert match, ".rp-answer has no rule"
    body = match.group(1)
    assert "background: none" in body
    assert "border: 0" in body
