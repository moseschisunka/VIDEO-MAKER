"""PR-1004 authentication, authorization, and path-control contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from lib.observability import metrics


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.release_blocker
def test_web_boundary_security_policy_is_explicit_and_fail_closed() -> None:
    policy = yaml.safe_load((REPO_ROOT / "config" / "security_policy.yaml").read_text(encoding="utf-8"))
    boundary = policy["web_boundary"]
    assert boundary["cors"] == {
        "mode": "same_origin_only",
        "allow_origins": [],
        "allow_credentials": False,
        "remote_override": "reverse_proxy_only",
    }
    assert boundary["csrf"]["mode"] == "bearer_authorization_header_only"
    assert boundary["csrf"]["cookie_authentication"] is False
    assert boundary["csrf"]["state_changing_methods_require_bearer"] is True
    assert boundary["request_rate_limit"]["enforcement"] == "trusted_reverse_proxy_required_for_remote"
    assert boundary["request_rate_limit"]["expected_rejection_status"] == 429


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", root)
    monkeypatch.setattr(server_mod, "PROJECTS_DIR", root)
    monkeypatch.setattr(server_mod, "_summary_cache", {})
    monkeypatch.setattr(
        server_mod,
        "_PROJECTS_ROOT_STR",
        os.path.normcase(str(root.resolve())),
    )
    return root


@pytest.fixture
def no_watch(monkeypatch: pytest.MonkeyPatch):
    async def _no_watch():
        return None

    monkeypatch.setattr(server_mod, "_watch_projects", _no_watch)


def _project(root: Path, project_id: str) -> Path:
    project = root / project_id
    (project / "artifacts").mkdir(parents=True)
    (project / "assets").mkdir()
    (project / "renders").mkdir()
    (project / "project.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "title": project_id.title(),
                "pipeline_type": "screen-demo",
                "created_at": "2026-09-02T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return project


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return TestClient(server_mod.create_app())


def test_local_loopback_defaults_to_unauthenticated(monkeypatch, no_watch):
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)

    with _client(monkeypatch) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "app": "backlot"}


@pytest.mark.release_blocker
def test_remote_binding_fails_closed_without_auth_configuration(monkeypatch, no_watch):
    monkeypatch.setenv("BACKLOT_HOST", "0.0.0.0")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)

    with _client(monkeypatch) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


@pytest.mark.release_blocker
def test_remote_binding_requires_exact_bearer_token(monkeypatch, no_watch):
    monkeypatch.setenv("BACKLOT_HOST", "0.0.0.0")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "test-token")

    with _client(monkeypatch) as client:
        missing = client.get("/api/health")
        wrong = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
        basic = client.get("/api/health", headers={"Authorization": "Basic test-token"})
        correct = client.get("/api/health", headers={"Authorization": "Bearer test-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert basic.status_code == 401
    assert correct.status_code == 200
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "test-token" not in missing.text


@pytest.mark.release_blocker
def test_auth_failures_are_observable_without_recording_credentials(monkeypatch, no_watch):
    monkeypatch.setenv("BACKLOT_HOST", "0.0.0.0")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "metric-token")

    def total() -> float:
        return sum(
            float(item["value"])
            for item in metrics.snapshot()["counters"]
            if item["name"] == "openmontage_auth_failures_total"
        )

    before = total()
    with _client(monkeypatch) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    after = total()

    assert response.status_code == 401
    assert after >= before + 1
    assert "wrong" not in response.text
    assert "metric-token" not in response.text


@pytest.mark.release_blocker
def test_explicit_auth_requirement_can_protect_loopback(monkeypatch, no_watch):
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.setenv("BACKLOT_AUTH_REQUIRED", "true")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "local-token")

    with _client(monkeypatch) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get(
            "/api/health", headers={"Authorization": "Bearer local-token"}
        ).status_code == 200


@pytest.mark.release_blocker
def test_project_scope_filters_library_and_denies_direct_access(
    monkeypatch, no_watch, projects_root: Path
):
    _project(projects_root, "allowed")
    _project(projects_root, "denied")
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("BACKLOT_PROJECT_SCOPE", "allowed")

    with _client(monkeypatch) as client:
        projects = client.get("/api/projects")
        allowed = client.get("/api/project/allowed/state")
        denied = client.get("/api/project/denied/state")

    assert projects.status_code == 200
    assert [item["project_id"] for item in projects.json()] == ["allowed"]
    assert allowed.status_code == 200
    assert denied.status_code == 403


@pytest.mark.release_blocker
def test_project_scope_supports_prefix_rules_for_variants(monkeypatch):
    monkeypatch.setenv("BACKLOT_PROJECT_SCOPE", "school-a-*")

    assert server_mod._project_is_allowed("school-a-lesson") is True
    assert server_mod._project_is_allowed("school-b-lesson") is False


def test_board_routes_validate_project_component(monkeypatch, no_watch, projects_root: Path):
    _project(projects_root, "film")
    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BACKLOT_PROJECT_SCOPE", raising=False)

    with _client(monkeypatch) as client:
        valid = client.get("/p/film")
        unknown = client.get("/p/no-such-project")
        traversal = client.get("/p/%2E%2E/AGENT_GUIDE.md")

    assert valid.status_code == 200
    assert unknown.status_code == 404
    assert traversal.status_code == 400


@pytest.mark.release_blocker
def test_project_root_symlink_is_rejected(monkeypatch, no_watch, projects_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, projects_root / "linked-project", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BACKLOT_PROJECT_SCOPE", raising=False)

    with _client(monkeypatch) as client:
        response = client.get("/api/project/linked-project/state")

    assert response.status_code == 403


@pytest.mark.release_blocker
def test_media_symlink_cannot_escape_project(monkeypatch, no_watch, projects_root: Path, tmp_path: Path):
    project = _project(projects_root, "film")
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve", encoding="utf-8")
    try:
        os.symlink(outside, project / "assets" / "leak.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    monkeypatch.setenv("BACKLOT_HOST", "127.0.0.1")
    monkeypatch.delenv("BACKLOT_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BACKLOT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BACKLOT_PROJECT_SCOPE", raising=False)

    with _client(monkeypatch) as client:
        response = client.get("/media/film/assets/leak.txt")

    assert response.status_code == 403
