"""Both languages must be complete, and neither may leak into the other."""

from __future__ import annotations

import re

import pytest

from app.ui.i18n import CATEGORIES, LANGUAGES, STRINGS, t


def test_both_languages_cover_the_same_keys():
    english, french = set(STRINGS["en"]), set(STRINGS["fr"])
    assert english == french, (
        f"only in en: {sorted(english - french)}; "
        f"only in fr: {sorted(french - english)}")


@pytest.mark.parametrize("language", ["en", "fr"])
def test_no_string_is_left_empty(language):
    for key, value in STRINGS[language].items():
        assert value.strip(), f"{language}:{key} is empty"


def test_french_is_written_not_left_in_english():
    """A French interface with English strings in it is worse than either."""
    for key in STRINGS["en"]:
        english, french = STRINGS["en"][key], STRINGS["fr"][key]
        # Short labels can legitimately coincide; prose cannot.
        if len(english.split()) > 4:
            assert english != french, f"{key} was never translated"


def test_every_category_asks_a_real_question_in_both_languages():
    for language in ("en", "fr"):
        for key, slug, question in CATEGORIES[language]:
            assert key in STRINGS[language], f"{key} has no label"
            assert question.strip().endswith("?"), f"{slug} is not a question"
            assert len(question.split()) >= 4, f"{slug} is too vague to answer"


def test_categories_match_across_languages():
    assert [slug for _, slug, _ in CATEGORIES["en"]] == \
           [slug for _, slug, _ in CATEGORIES["fr"]]


def test_placeholders_are_supplied_everywhere_they_are_used():
    """A stray {name} rendered to a user is a bug that reads as broken text."""
    for language in ("en", "fr"):
        for key, value in STRINGS[language].items():
            for field in re.findall(r"\{(\w+)\}", value):
                assert field in {"provider", "n", "mins", "q"}, \
                    f"{language}:{key} expects unknown field {field}"


def test_languages_are_named_in_their_own_language():
    assert dict(LANGUAGES) == {"en": "English", "fr": "Français"}


def test_lookup_falls_back_without_crashing():
    assert t("de", "submit") == STRINGS["en"]["submit"]
    assert t("en", "no_such_key") == "no_such_key"
