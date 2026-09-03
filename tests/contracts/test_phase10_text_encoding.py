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
    assert 'id="projectTitle" class="form-input" maxlength="240"' in html
    assert 'id="projectTopic" class="form-textarea" rows="3" maxlength="20000"' in html
    assert 'aria-label="Close video creation wizard"' in html
    assert 'wizardModal.setAttribute("aria-hidden", "false")' in library_js
    assert 'wizardModal.setAttribute("aria-hidden", "true")' in library_js
    assert 'event.key === "Escape"' in library_js
    assert 'event.key === "Enter" || event.key === " "' in library_js
    assert '.selector-card:focus-visible, .playbook-card:focus-visible' in board_css


@pytest.mark.release_blocker
def test_creation_wizard_uses_authoritative_catalogs_and_fails_closed() -> None:
    html = (REPO_ROOT / "backlot" / "ui" / "index.html").read_text(encoding="utf-8")
    library_js = (REPO_ROOT / "backlot" / "ui" / "library.js").read_text(
        encoding="utf-8"
    )

    assert 'id="wizardOptionsStatus"' in html
    assert 'id="retryWizardOptionsBtn"' in html
    assert 'let wizardOptionsState = "idle"' in library_js
    assert 'if (wizardOptionsState !== "ready")' in library_js
    assert 'Current production options could not be loaded. Retry before creating a video.' in library_js
    assert 'const pipelines = availablePipelines;' in library_js
    assert 'const playbooks = compatiblePlaybooksForSelectedPipeline();' in library_js
    assert 'const voices = availableVoices;' in library_js
    assert 'const validPipelines = Array.isArray(pipelines)' in library_js
    assert 'const validVoices = Array.isArray(voices)' in library_js
    assert 'normalizeWizardSelections();' in library_js
    assert 'compatiblePlaybooksForSelectedPipeline' in library_js
    pipeline_loader = (REPO_ROOT / "lib" / "pipeline_loader.py").read_text(encoding="utf-8")
    assert '"compatible_playbooks": _compatible_playbook_ids(manifest)' in pipeline_loader
    assert 'pipeline && pipeline.id === selectedPipeline && pipeline.creation_enabled === true' in library_js
    assert 'availablePipelines.length ? availablePipelines :' not in library_js
    assert 'availablePlaybooks.length ? availablePlaybooks :' not in library_js
    assert 'availableVoices.length ? availableVoices :' not in library_js


@pytest.mark.release_blocker
def test_pipeline_start_errors_are_visible_to_the_creator() -> None:
    library_js = (REPO_ROOT / "backlot" / "ui" / "library.js").read_text(
        encoding="utf-8"
    )
    board_js = (REPO_ROOT / "backlot" / "ui" / "board.js").read_text(
        encoding="utf-8"
    )

    assert "if (!runResponse.ok || runData.ok !== true)" in library_js
    assert "Project created, but automatic run could not start:" in library_js
    assert "if (!response.ok || data.ok !== true)" in board_js
    assert "alert(`Run pipeline failed:" in board_js
