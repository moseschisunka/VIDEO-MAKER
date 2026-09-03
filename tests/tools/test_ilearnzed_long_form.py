"""Regression tests for iLearnZed long-form production constraints."""

from pathlib import Path

from lib.content_templates import list_content_templates
from lib.creator_profile import load_creator_profile
from lib.media_profiles import get_profile, validate_duration


def test_ilearnzed_long_form_profile_is_content_led_hd() -> None:
    profile = get_profile("ilearnzed_long_form")

    assert (profile.width, profile.height, profile.fps) == (1920, 1080, 30)
    assert profile.max_duration_seconds is None


def test_ilearnzed_long_form_accepts_longer_content_led_lessons() -> None:
    validate_duration("ilearnzed_long_form", 600)
    validate_duration("ilearnzed_long_form", 3600)


def test_ilearnzed_profile_and_templates_load() -> None:
    profile = load_creator_profile("profiles/ilearnzed.yaml")
    templates = list_content_templates(Path("content_templates"))

    assert profile["brand"]["name"] == "iLearnZed"
    assert profile["audience"]["learner_levels"] == [
        "Form 1", "Form 2", "Form 3", "Form 4", "Form 5", "Form 6"
    ]
    assert len(templates) == 6
