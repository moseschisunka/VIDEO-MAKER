"""Tests for style playbooks: schema validation, loader functions, and accessibility checks."""

from __future__ import annotations

from pathlib import Path
import pytest

from styles.playbook_loader import (
    STYLES_DIR,
    list_playbooks,
    load_playbook,
    validate_playbook,
    validate_contrast,
)


def test_all_playbooks_load_and_validate():
    """Verify every playbook YAML in styles/ validates against the playbook schema."""
    playbook_names = list_playbooks()
    assert len(playbook_names) >= 6, f"Expected at least 6 playbooks, got {len(playbook_names)}"
    assert "ink-sketch" in playbook_names, "ink-sketch playbook should be in list_playbooks()"

    for name in playbook_names:
        pb = load_playbook(name)
        assert pb is not None
        assert "identity" in pb
        assert "visual_language" in pb
        assert "typography" in pb
        assert "motion" in pb
        assert "audio" in pb
        assert "asset_generation" in pb
        validate_playbook(pb)


def test_ink_sketch_playbook_properties():
    """Verify ink-sketch specific color palette, typography, and motion rules."""
    pb = load_playbook("ink-sketch")
    assert pb["identity"]["name"] == "Ink Sketch"
    assert pb["identity"]["category"] == "whiteboard"

    palette = pb["visual_language"]["color_palette"]
    assert palette["background"].upper() == "#FFFFFF"
    assert "#1A1A1A" in palette["primary"]
    assert "#FF6B35" in palette["accent"]  # Orange flow accent

    # Verify typography has Patrick Hand
    assert pb["typography"]["headings"]["font"] == "Patrick Hand"
    assert pb["typography"]["body"]["font"] == "Patrick Hand"


def test_contrast_ratio_helper():
    """Verify WCAG contrast calculation for black on white."""
    result = validate_contrast("#000000", "#FFFFFF")
    assert result["ratio"] >= 20.0  # Pure black on pure white is 21:1
    assert result["normal_text"]["AA"] is True
