"""PR-103 pre-mutation create-request contracts.

The endpoint may reject a request for many reasons, but none of those
rejections may create a project directory or any partial artifact.  These
tests exercise the request boundary without invoking a provider or renderer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from lib.pipeline_loader import load_pipeline_readonly
from lib.work_order import read_work_order


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR", str(projects.resolve()).lower())

    async def no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
    with TestClient(server_mod.create_app()) as test_client:
        yield test_client, projects


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"title": "Unknown", "pipeline_type": "not-a-pipeline"}, "unknown pipeline_type"),
        ({"title": "Held", "pipeline_type": "animated-explainer"}, "not enabled"),
        ({"title": "Bad playbook", "playbook": "not-a-playbook"}, "invalid playbook"),
        ({"title": "Incompatible playbook", "playbook": "ilearnzed-education"}, "not compatible"),
        ({"title": "Bad runtime", "render_runtime": "hyperframes"}, "not supported"),
        ({"title": "Bad source", "source_mode": "stock_retrieval"}, "not declared"),
        ({"title": "Bad profile", "output_profile": "cinematic"}, "not supported"),
        ({"title": "Conflicting profile", "output_profile": "youtube_landscape", "profile": "youtube_shorts"}, "must match"),
        ({"title": "Bad aspect", "output_profile": "youtube_shorts", "aspect_ratio": "16:9"}, "does not match"),
        ({"title": "Bad voice", "voice": "not a voice"}, "voice must"),
        ({"title": "Bad provider", "voice_provider": "edge_tts", "tts_provider": "openai"}, "must match"),
        ({"title": "Bad id", "project_id": "../escape"}, "project_id must"),
    ],
)
def test_rejected_create_requests_leave_no_project(
    client, payload: dict, needle: str
) -> None:
    test_client, projects = client

    response = test_client.post("/api/project/create", json=payload)

    assert response.status_code == 400
    assert needle.lower() in str(response.json().get("detail", "")).lower()
    assert list(projects.iterdir()) == []


@pytest.mark.parametrize("payload, field, expected", [
    ({"tts_provider": "open-ai", "voice": "alloy"}, "voice_provider", "openai"),
    ({"profile": "youtube_shorts"}, "output_profile", "youtube_shorts"),
])
def test_legacy_alias_without_canonical_field_overrides_default(client, payload, field, expected):
    test_client, projects = client
    response = test_client.post("/api/project/create", json={"title": "Legacy client", **payload})
    assert response.status_code == 200, response.text
    config = json.loads(
        (projects / response.json()["project_id"] / "artifacts" / "project_config.json").read_text(encoding="utf-8")
    )
    assert config[field] == expected


@pytest.mark.parametrize("action", ["approve", "revise"])
def test_human_decision_conflicts_return_409(client, monkeypatch, action):
    from lib.work_order import WorkOrderConflictError

    test_client, _ = client
    created = test_client.post("/api/project/create", json={"title": "Approval conflict"})
    assert created.status_code == 200

    def conflict(*args, **kwargs):
        raise WorkOrderConflictError("another agent owns this run")

    monkeypatch.setattr(server_mod, "decide_human_gate", conflict)
    response = test_client.post(
        f"/api/project/{created.json()['project_id']}/{action}", json={"stage": "idea"},
    )
    assert response.status_code == 409
    assert "another agent" in response.json()["detail"]


def test_valid_create_persists_every_explicit_selection(client) -> None:
    test_client, projects = client
    response = test_client.post(
        "/api/project/create",
        json={
            "title": "  Screen walkthrough  ",
            "topic_prompt": "  Show the install flow. ",
            "pipeline_type": " SCREEN-DEMO ",
            "playbook": " PREMIUM-MINIMALIST ",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": " REMOTION ",
            "output_profile": " YOUTUBE_LANDSCAPE ",
            "target_duration_seconds": 45,
            "source_mode": "synthetic_terminal",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    project = projects / body["project_id"]
    assert project.is_dir()
    proposal = json.loads((project / "artifacts" / "proposal_packet.json").read_text(encoding="utf-8"))
    config = json.loads((project / "artifacts" / "project_config.json").read_text(encoding="utf-8"))
    marker = json.loads((project / "project.json").read_text(encoding="utf-8"))

    for artifact in (proposal, config, marker):
        assert artifact["pipeline_type"] == "screen-demo"
        assert artifact["voice_provider"] == "edge_tts"
        assert artifact["render_runtime"] == "remotion"
        assert artifact["output_profile"] == "youtube_landscape"
        assert artifact["aspect_ratio"] == "16:9"
        assert artifact["source_mode"] == "synthetic_terminal"

    assert proposal["playbook"] == "premium-minimalist"
    assert config["playbook"] == "premium-minimalist"
    assert marker["style_playbook"] == "premium-minimalist"

    assert proposal["title"] == "Screen walkthrough"
    assert proposal["topic_prompt"] == "Show the install flow."
    assert proposal["target_duration_seconds"] == 45
    assert config["tts_provider"] == "edge_tts"
    assert body["production_ready"] is False
    assert body["production_gate"] == "PR-11G"
    assert body["identity"]["valid"] is True
    assert body["identity"]["expected"]["project_id"] == body["project_id"]
    assert body["identity"]["expected"]["pipeline_type"] == "screen-demo"

    work_order = read_work_order(
        project,
        manifest=load_pipeline_readonly("screen-demo"),
    )
    assert body["work_order"]["run_id"] == work_order["run_id"]
    assert body["identity"]["expected"]["run_id"] == work_order["run_id"]
    assert work_order["project_id"] == body["project_id"]
    assert work_order["pipeline_type"] == "screen-demo"
    assert work_order["next_stage"] == "idea"
    assert [stage["name"] for stage in work_order["stages"]] == [
        stage["name"] for stage in load_pipeline_readonly("screen-demo")["stages"]
    ]
    assert work_order["selections"]["output_profile"] == "youtube_landscape"


def test_provider_aliases_do_not_create_a_false_conflict(client) -> None:
    test_client, projects = client

    response = test_client.post(
        "/api/project/create",
        json={"title": "Alias-compatible voice", "tts_provider": "edge"},
    )

    assert response.status_code == 200, response.text
    project = projects / response.json()["project_id"]
    proposal = json.loads((project / "artifacts" / "proposal_packet.json").read_text(encoding="utf-8"))
    config = json.loads((project / "artifacts" / "project_config.json").read_text(encoding="utf-8"))
    assert proposal["voice_provider"] == "edge_tts"
    assert config["tts_provider"] == "edge_tts"
