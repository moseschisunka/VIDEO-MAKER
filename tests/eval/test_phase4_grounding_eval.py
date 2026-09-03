"""PR-408 deterministic factual accuracy evaluation corpus."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from lib.grounding import validate_grounding


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "tests" / "eval" / "grounding_cases.json"
SCHEMA_PATH = ROOT / "schemas" / "evals" / "grounding_case.schema.json"


def test_grounding_corpus_schema_and_expected_outcomes():
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert {case["expected_status"] for case in cases} == {
        "supported", "contradicted", "uncertain", "missing_source"
    }
    for case in cases:
        jsonschema.validate(case, schema)
        report = validate_grounding(case["research_brief"], case["script"], strict=True)
        assert report["decision"] == case["expected_decision"], case["id"]
        assert report["claims"][0]["status"] == case["expected_status"], case["id"]
