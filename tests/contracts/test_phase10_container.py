"""PR-1003 static container/deployment contract checks.

The workstation used for this review does not provide a Docker daemon, so the
actual image build is a CI gate. These checks catch configuration regressions
before that build runs and ensure the image cannot accidentally bake secrets or
reopen the unauthenticated local board to every network interface.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_has_pinned_runtime_non_root_user_and_healthcheck() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM node:\d+\.\d+\.\d+-bookworm@sha256:[0-9a-f]{64}\s*$", dockerfile, re.MULTILINE)
    assert "USER node" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "BACKLOT_AUTH_TOKEN" in dockerfile
    assert "authorization:'Bearer '+t" in dockerfile
    assert "npm ci --prefix remotion-composer" in dockerfile
    assert "npx --no-install remotion browser ensure" in dockerfile
    assert "python3 -m pip install --break-system-packages --no-cache-dir ." in dockerfile
    assert "COPY .env" not in dockerfile
    assert not re.search(r"^(?:ARG|ENV)\s+\w*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=", dockerfile, re.MULTILINE | re.IGNORECASE)


def test_dockerignore_excludes_credentials_and_user_outputs() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    ignored = {line.strip() for line in dockerignore if line.strip() and not line.lstrip().startswith("#")}
    assert ".env" in ignored
    assert ".env.*" in ignored
    assert "projects/*" in ignored
    assert "output/*" in ignored
    assert "remotion-composer/node_modules" in ignored
    assert "tmp" in ignored
    assert "*.pem" in ignored
    assert "*.key" in ignored


def test_compose_is_local_only_and_hardened() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["openmontage-backlot"]
    assert service["ports"] == ["127.0.0.1:4750:4750"]
    assert service["read_only"] is True
    assert "/app/projects" in service["volumes"][0]
    assert "/app/output" in service["volumes"][1]
    assert "ALL" in service["cap_drop"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert "/home/node/.npm" in service["tmpfs"]
    assert service["healthcheck"]["retries"] == 3
    health_command = " ".join(str(item) for item in service["healthcheck"]["test"])
    assert "BACKLOT_AUTH_TOKEN" in health_command
    assert "authorization:'Bearer '+t" in health_command


def test_ci_container_gate_checks_the_baked_remotion_browser() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Verify the baked Remotion browser with an in-image still" in workflow
    assert "npx --no-install remotion browser ensure" in workflow
    assert "npx --no-install remotion still src/index.tsx EndTag /tmp/container-still.png --frame=0" in workflow
    assert "npx --no-install remotion still src/index.tsx ProductReveal /tmp/container-product-still.png --frame=120" in workflow
    assert "npx --no-install remotion still src/index.tsx SignalFromTomorrowWithMusic /tmp/container-signal-still.png --frame=0" in workflow
    assert "npx --no-install remotion still src/index.tsx TalkingHead /tmp/container-talking-head-still.png --frame=0" in workflow
    assert "npx --no-install remotion still src/index.tsx TitledVideo /tmp/container-titled-video-still.png --frame=0" in workflow
    assert "npx --no-install remotion still src/index.tsx LyricOverlay /tmp/container-lyric-still.png --frame=0" in workflow
    assert "openmontage-container-render" in workflow


def test_ci_jobs_use_the_environment_where_make_installs_dependencies() -> None:
    """CI must run pytest/scripts from the venv populated by make install-dev."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: python -m pytest tests" not in workflow
    assert "run: python -m pytest tests/contracts" not in workflow
    assert ".venv/bin/python -m pytest tests/contracts" in workflow
    assert ".venv/bin/python -m pytest tests -m \"not live_provider and not hyperframes_qa\" -q" in workflow
    assert ".venv/bin/python scripts/measure_slos.py" in workflow
    assert ".venv/bin/python scripts/measure_load_soak.py" in workflow
    assert ".venv/bin/python scripts/run_operations_drill.py" in workflow


def test_ci_waits_for_docker_healthcheck_state_after_http_health() -> None:
    """An early HTTP 200 must not be mistaken for Docker HEALTHCHECK success."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    wait_marker = "for health_attempt in $(seq 1 30); do"
    final_check = "test \"$(docker inspect --format='{{.State.Health.Status}}' openmontage-backlot-ci)\" = \"healthy\""
    assert wait_marker in workflow
    assert "healthy)" in workflow
    assert "unhealthy)" in workflow
    assert workflow.index(wait_marker) < workflow.rindex(final_check)
