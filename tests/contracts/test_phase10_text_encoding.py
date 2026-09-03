"""PR-10G text-integrity contracts for Windows and other non-UTF-8 locales.

Pipeline manifests, style playbooks, and runtime configuration are authored as
UTF-8.  Opening them without an explicit encoding lets Windows' active code
page (often cp1252) turn punctuation such as an em dash into mojibake before
the API or renderer sees it.  These release-blocking checks exercise the real
loaders against the repository's non-ASCII content so the UI, narration
metadata, and render decisions retain their authored text exactly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib.config_model import OpenMontageConfig
from lib.pipeline_loader import load_pipeline
from lib.playbook_generator import load_existing_playbook
from styles.playbook_loader import load_playbook


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.release_blocker
def test_pipeline_manifest_utf8_text_survives_loading() -> None:
    manifest = load_pipeline("screen-demo", defs_dir=REPO_ROOT / "pipeline_defs")

    description = manifest["description"]
    assert "REAL CAPTURE —" in description
    assert "SYNTHETIC (Remotion TerminalScene) —" in description
    assert "â" not in description


@pytest.mark.release_blocker
def test_playbook_utf8_text_survives_both_loaders() -> None:
    loaded = load_playbook("minimalist-diagram", styles_dir=REPO_ROOT / "styles")
    generated_path_loader = load_existing_playbook("minimalist-diagram")

    for playbook in (loaded, generated_path_loader):
        rules = playbook["quality_rules"]
        assert any("—" in rule for rule in rules)
        assert all("â" not in rule for rule in rules)


@pytest.mark.release_blocker
def test_runtime_config_utf8_text_survives_loading() -> None:
    # Keep the fixture inside the checkout: some supported Windows runners
    # deny pytest's default per-user temp root to the test process.
    temp_dir = REPO_ROOT / "tmp"
    temp_dir.mkdir(exist_ok=True)
    config_path = temp_dir / f"phase10-encoding-{os.getpid()}.yaml"
    config_path.write_text(
        'creator_profile: "profiles/teacher — africa.yaml"\n',
        encoding="utf-8",
    )
    try:
        config = OpenMontageConfig.load(config_path)
    finally:
        config_path.unlink(missing_ok=True)

    assert config.creator_profile == "profiles/teacher — africa.yaml"
    assert "â" not in config.creator_profile


@pytest.mark.release_blocker
def test_creation_wizard_guards_title_only_submissions() -> None:
    library_js = (REPO_ROOT / "backlot" / "ui" / "library.js").read_text(
        encoding="utf-8"
    )

    assert 'if (!title)' in library_js
    assert 'if (!topic)' in library_js
    assert 'Please describe the video topic and key takeaways.' in library_js


@pytest.mark.release_blocker
def test_creation_wizard_controls_are_accessible_and_keyboard_selectable() -> None:
    html = (REPO_ROOT / "backlot" / "ui" / "index.html").read_text(encoding="utf-8")
    library_js = (REPO_ROOT / "backlot" / "ui" / "library.js").read_text(
        encoding="utf-8"
    )
    board_css = (REPO_ROOT / "backlot" / "ui" / "board.css").read_text(
        encoding="utf-8"
    )

    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="createWizardTitle"' in html
    assert 'for="projectTitle"' in html
    assert 'for="projectTopic"' in html
    assert 'aria-label="Close video creation wizard"' in html
    assert 'wizardModal.setAttribute("aria-hidden", "false")' in library_js
    assert 'wizardModal.setAttribute("aria-hidden", "true")' in library_js
    assert 'event.key === "Escape"' in library_js
    assert 'event.key === "Enter" || event.key === " "' in library_js
    assert '.selector-card:focus-visible, .playbook-card:focus-visible' in board_css
