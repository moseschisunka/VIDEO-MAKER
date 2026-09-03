"""Small, dependency-free redaction helpers for logs and persisted events.

Provider adapters are intentionally allowed to see their credentials, but
diagnostics, Backlot events, and structured provider errors are not.  This
module keeps the boundary in one place and redacts both configured environment
values and common token-bearing text such as bearer headers and signed URLs.
It is defensive rather than a substitute for a secret manager: callers must
still avoid putting raw credentials in prompts, files, or command arguments.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Iterable


# Keep this inventory in sync with the provider environment contract and the
# Backlot runtime settings.  Values are read at call time so tests and secret
# rotation do not require a process restart for redaction to take effect.
SECRET_ENV_NAMES = frozenset(
    {
        "BACKLOT_AUTH_TOKEN",
        "FAL_KEY",
        "FAL_AI_API_KEY",
        "MINIMAX_API_KEY",
        "REPLICATE_API_TOKEN",
        "HIGGSFIELD_API_KEY",
        "HIGGSFIELD_API_SECRET",
        "HIGGSFIELD_KEY",
        "KLING_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "DOUBAO_SPEECH_API_KEY",
        "DASHSCOPE_API_KEY",
        "SUNO_API_KEY",
        "HEYGEN_API_KEY",
        "RUNWAY_API_KEY",
        "PEXELS_API_KEY",
        "PIXABAY_API_KEY",
        "UNSPLASH_ACCESS_KEY",
        "HF_TOKEN",
        "AZURE_SPEECH_KEY",
        "WAV2LIP_PATH",
        "SADTALKER_PATH",
        "FISH_AUDIO_API_KEY",
        "FREESOUND_API_KEY",
        "FAL_KEY",
        "FAL_AI_API_KEY",
        "TENCENT_TOKENHUB_API_KEY",
        "VOLC_ACCESSKEY",
        "VOLC_SECRETKEY",
        "FAL_AI_API_KEY",
    }
)

_SECRET_KEY_WORDS = (
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "credential",
    "private_key",
    "signed_url",
    "signature",
)
_SECRET_KEY_PATTERN = "(?:" + "|".join(re.escape(word) for word in _SECRET_KEY_WORDS) + ")"

# Handles Python/CLI-style ``api_key=value`` and ``token: value`` strings.
_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>\b{_SECRET_KEY_PATTERN}\b\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\"'\s,;&}}\]]+)(?P=quote)",
    re.IGNORECASE,
)
# Handles JSON-ish ``\"api_key\": \"value\"`` strings where the quote is
# between the key and the colon.
_JSON_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>[\"']{_SECRET_KEY_PATTERN}[\"']\s*:\s*[\"'])(?P<value>.*?)(?P<suffix>[\"'])",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET_RE = re.compile(
    rf"(?i)([?&][A-Za-z0-9_-]*(?:{_SECRET_KEY_PATTERN})=)([^&#\s]+)"
)


def _environment_secret_values() -> list[str]:
    values: set[str] = set()
    for name, value in os.environ.items():
        upper = name.upper()
        if name in SECRET_ENV_NAMES or any(
            marker in upper for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY")
        ):
            cleaned = str(value).strip()
            # Empty values and tiny values ("on", "no", etc.) are not useful
            # secrets and redacting them would damage otherwise safe output.
            if len(cleaned) >= 4:
                values.add(cleaned)
    return sorted(values, key=len, reverse=True)


def redact_text(value: Any, *, extra_secrets: Iterable[str] | None = None) -> str:
    """Return text with credentials, bearer tokens, and signed URL values masked."""
    text = str(value)
    for secret in [*_environment_secret_values(), *(extra_secrets or ())]:
        secret = str(secret)
        if len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _JSON_ASSIGNMENT_RE.sub(r"\g<prefix>[REDACTED]\g<suffix>", text)
    text = _ASSIGNMENT_RE.sub(r"\g<prefix>[REDACTED]", text)
    return text


def redact_mapping(value: Any, *, extra_secrets: Iterable[str] | None = None) -> Any:
    """Recursively redact a JSON-like payload without changing its shape."""
    if isinstance(value, Mapping):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if any(word in normalized for word in _SECRET_KEY_WORDS):
                output[key] = "[REDACTED]"
            else:
                output[key] = redact_mapping(item, extra_secrets=extra_secrets)
        return output
    if isinstance(value, list):
        return [redact_mapping(item, extra_secrets=extra_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item, extra_secrets=extra_secrets) for item in value)
    if isinstance(value, str):
        return redact_text(value, extra_secrets=extra_secrets)
    return value


__all__ = ["SECRET_ENV_NAMES", "redact_mapping", "redact_text"]
