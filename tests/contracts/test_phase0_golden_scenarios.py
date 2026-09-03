"""Phase 0 golden scenario contract tests.

These tests validate the evaluation briefs themselves. They do not call providers,
render media, or certify a production pipeline; they ensure every required scenario
is deterministic to load and explicit about its contracts and prohibited downgrades.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = PROJECT_ROOT / "tests" / "eval" / "golden_scenarios"
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "evals" / "golden_scenario.schema.json"

REQUIRED_SCENARIOS = {
    "social_short_15s": "talking-head",
    "grounded_explainer_30s": "animated-explainer",
    "stock_documentary_60s": "documentary-montage",
    "talking_head_basic": "talking-head",
    "screen_demo_30s": "screen-demo",
    "mixed_media_45s": "hybrid",
    "hyperframes_kinetic_typography_20s": "animation",
    "lesson_5m": "animated-explainer",
}

SECRET_OR_ABSOLUTE_PATH = re.compile(
    r"(?:API[_-]?KEY|ACCESS[_-]?TOKEN|SECRET|PASSWORD)|"
    r"(?i:[A-Z]:\\|/home/|/Users/)"
)


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIO_DIR.glob("*.json"))


def _load_scenarios() -> list[tuple[Path, dict]]:
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in _scenario_paths()]


def test_exact_required_scenario_set_is_present() -> None:
    paths = _scenario_paths()
    assert {path.stem for path in paths} == set(REQUIRED_SCENARIOS)
    assert len(paths) == 8


def test_every_scenario_matches_strict_schema() -> None:
    schema = _load_schema()
    for path, scenario in _load_scenarios():
        jsonschema.validate(instance=scenario, schema=schema)
        assert scenario["pipeline_type"] == REQUIRED_SCENARIOS[path.stem]
        assert scenario["name"] == path.stem


def test_scenarios_have_unique_names_and_explicit_runtime_candidates() -> None:
    scenarios = _load_scenarios()
    names = [scenario["name"] for _, scenario in scenarios]
    assert len(names) == len(set(names)) == 8
    for _, scenario in scenarios:
        assert scenario["runtime_candidates"]
        assert "render_runtime" in scenario["expected_artifacts"]["edit_decisions"]["required_fields"]


def test_scenarios_declare_duration_consistently() -> None:
    for path, scenario in _load_scenarios():
        assert scenario["duration_seconds"] == scenario["inputs"]["duration_seconds"], path
        assert scenario["duration_tolerance_seconds"] >= 0
        assert scenario["duration_seconds"] > scenario["duration_tolerance_seconds"]


def test_scenarios_have_approval_and_no_downgrade_contracts() -> None:
    for path, scenario in _load_scenarios():
        assert len(scenario["expected_approvals"]) >= 1, path
        assert len(scenario["prohibited_downgrade"]) >= 1, path
        assert any(
            "approval" in item.lower() or "approved" in item.lower()
            for item in scenario["prohibited_downgrade"]
        ), path


def test_scenarios_contain_no_secrets_or_developer_absolute_paths() -> None:
    for path in _scenario_paths():
        raw = path.read_text(encoding="utf-8")
        assert not SECRET_OR_ABSOLUTE_PATH.search(raw), path
