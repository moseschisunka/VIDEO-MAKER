"""PR-1005 secret-redaction and privacy-policy contracts."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from lib.events import emit_event
from lib.providers.contracts import ProviderError
from lib.secrets import redact_mapping, redact_text
from tools.base_tool import ToolResult


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_redaction_masks_environment_credentials_bearer_and_signed_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openmontage-secret-123")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "backlot-secret-456")
    raw = (
        "OPENAI_API_KEY=sk-openmontage-secret-123; "
        "Authorization: Bearer backlot-secret-456; "
        "https://cdn.example/video.mp4?X-Amz-Signature=signed-secret-789"
    )

    safe = redact_text(raw)

    assert "sk-openmontage-secret-123" not in safe
    assert "backlot-secret-456" not in safe
    assert "signed-secret-789" not in safe
    assert safe.count("[REDACTED]") >= 3


def test_redaction_preserves_safe_structure_and_provider_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-inline-secret")
    monkeypatch.setenv("BACKLOT_AUTH_TOKEN", "inline-secret")
    payload = {
        "provider": "openai",
        "api_key": "sk-inline-secret",
        "nested": [{"signed_url": "https://cdn.example/a?sig=inline-secret"}],
        "attempt": 2,
    }

    safe = redact_mapping(payload, extra_secrets=["sk-inline-secret", "inline-secret"])
    error = ProviderError(
        code="provider_call_failed",
        message="request failed with sk-inline-secret",
        details={"authorization": "Bearer inline-secret", "attempt": 2},
    )
    result = ToolResult(success=False, error="tool failed with sk-inline-secret")

    assert safe["provider"] == "openai"
    assert safe["attempt"] == 2
    assert safe["api_key"] == "[REDACTED]"
    assert "inline-secret" not in json.dumps(safe)
    assert "sk-inline-secret" not in error.message
    assert error.details["authorization"] == "[REDACTED]"
    assert "sk-inline-secret" not in (result.error or "")


def test_event_redaction_prevents_secret_and_signed_url_persistence(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-event-secret")
    emit_event(
        project,
        {
            "event": "error",
            "error": "provider failed for sk-event-secret",
            "url": "https://cdn.example/a?X-Amz-Signature=event-signature",
        },
    )

    raw = (project / "events.jsonl").read_text(encoding="utf-8")
    assert "sk-event-secret" not in raw
    assert "event-signature" not in raw
    assert "[REDACTED]" in raw


def test_security_policy_and_docs_define_honest_retention_and_disclosure():
    policy = yaml.safe_load(
        (REPO_ROOT / "config" / "security_policy.yaml").read_text(encoding="utf-8")
    )
    docs = (REPO_ROOT / "docs" / "operations" / "SECRETS_PRIVACY_RETENTION.md").read_text(
        encoding="utf-8"
    )
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert policy["authentication"]["remote_binding_requires_token"] is True
    assert policy["secrets"]["redaction_boundary"] == "events_provider_errors_tool_results"
    assert policy["retention"]["enforcement_status"] == "policy_defaults_pending_automated_purge"
    assert policy["retention"]["deletion"]["preserve_minimal_audit_record"] is True
    assert policy["provider_disclosure"]["required_before_external_transfer"] is True
    assert "provider_retention_is_not_guaranteed_by_openmontage" == policy["provider_disclosure"]["retention_statement"]
    assert "Provider disclosure" in docs
    assert "automated purge" in docs.lower()
    assert "OPENMONTAGE_USER_MEDIA_RETENTION_DAYS=30" in env_example
    assert "BACKLOT_AUTH_TOKEN=" in env_example
