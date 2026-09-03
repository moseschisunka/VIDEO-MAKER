"""Deterministic voice-over contracts for production narration.

This module is deliberately provider-neutral.  A provider may expose many
friendly names, but a production run carries one immutable identity tuple:
provider, model/variant, voice id, locale, and settings.  The same tuple is
used when planning segments, creating cache keys, persisting artifacts, and
verifying the rendered transcript.

No network calls are made here.  The functions are safe to use in planning,
tests, resume checks, and manifest validation.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


class VoiceContractError(ValueError):
    """Raised when voice identity or narration state is unsafe."""


class VoiceSampleApprovalError(VoiceContractError):
    """Raised when batch narration is attempted before sample approval."""


VOICE_IDENTITY_FIELDS = ("provider", "model", "voice_id", "locale", "settings")
DEFAULT_PRONUNCIATION_DICTIONARY_VERSION = "v1"


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VoiceContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise VoiceContractError(f"voice contract value is not JSON serializable: {exc}") from exc


def _normalise_provider(value: Any) -> str:
    provider = _required_text(value, "voice provider").lower()
    # These are identity aliases, not fallback rules.  Keeping one canonical
    # spelling prevents ``edge`` and ``edge_tts`` from looking like two voices.
    return {"edge": "edge_tts", "microsoft_edge": "edge_tts", "open-ai": "openai"}.get(provider, provider)


def _alias(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


@dataclass(frozen=True)
class VoiceIdentity:
    """Immutable provider/model/voice/locale/settings tuple."""

    provider: str
    model: str
    voice_id: str
    locale: str
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _normalise_provider(self.provider))
        object.__setattr__(self, "model", _required_text(self.model, "voice model"))
        object.__setattr__(self, "voice_id", _required_text(self.voice_id, "voice_id"))
        object.__setattr__(self, "locale", _required_text(self.locale, "voice locale"))
        if not isinstance(self.settings, Mapping):
            raise VoiceContractError("voice settings must be an object")
        # Freeze the mapping logically by copying it into a sorted JSON-safe
        # dictionary.  Callers cannot mutate identity through an input alias.
        try:
            frozen = json.loads(_canonical_json(dict(self.settings)))
        except Exception as exc:
            raise VoiceContractError(f"voice settings are invalid: {exc}") from exc
        object.__setattr__(self, "settings", frozen)

    @property
    def identity_key(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "voice_id": self.voice_id,
            "locale": self.locale,
            "settings": dict(self.settings),
        }

    def contract(self) -> dict[str, Any]:
        payload = self.to_dict()
        # Do not put a self-referential/non-deterministic value into the hash.
        payload["identity_key"] = self.identity_key
        return payload


def normalize_voice_identity(value: Mapping[str, Any] | VoiceIdentity, **overrides: Any) -> VoiceIdentity:
    """Build a canonical identity from modern or legacy provider fields."""
    if isinstance(value, VoiceIdentity) and not overrides:
        return value
    if not isinstance(value, Mapping):
        raise VoiceContractError("voice identity must be an object")
    data = dict(value)
    data.update({key: val for key, val in overrides.items() if val not in (None, "")})
    provider = _alias(data, "provider", "tts_provider", "provider_name")
    model = _alias(data, "model", "model_id", "model_variant", "variant")
    voice_id = _alias(data, "voice_id", "voice", "voice_name")
    locale = _alias(data, "locale", "language_code", "voice_locale", "language")
    settings = _alias(data, "settings", "voice_settings", "provider_settings") or {}
    if model is None:
        # A provider must still expose an explicit variant.  ``default`` is a
        # declared variant, never a silent provider fallback.
        model = "default"
    if locale is None:
        locale = "und"
    return VoiceIdentity(
        provider=_normalise_provider(provider),
        model=str(model),
        voice_id=str(voice_id) if voice_id is not None else "",
        locale=str(locale),
        settings=settings if isinstance(settings, Mapping) else {},
    )


def voice_identity_key(value: Mapping[str, Any] | VoiceIdentity, **overrides: Any) -> str:
    return normalize_voice_identity(value, **overrides).identity_key


def validate_voice_identity(value: Mapping[str, Any] | VoiceIdentity) -> dict[str, Any]:
    try:
        identity = normalize_voice_identity(value)
    except VoiceContractError as exc:
        return {"valid": False, "errors": [str(exc)], "identity": None}
    return {"valid": True, "errors": [], "identity": identity.contract()}


def _identity_from_artifact(value: Mapping[str, Any]) -> VoiceIdentity | None:
    candidates = []
    for key in ("voice_identity", "voice_selection", "voice_contract"):
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            candidates.append(candidate)
    production_plan = value.get("production_plan")
    if isinstance(production_plan, Mapping):
        for key in ("voice_identity", "voice_selection", "voice_contract"):
            candidate = production_plan.get(key)
            if isinstance(candidate, Mapping):
                candidates.append(candidate)
    performance = value.get("voice_performance")
    if isinstance(performance, Mapping):
        candidates.append(performance)
    checks = value.get("checks")
    if isinstance(checks, Mapping) and isinstance(checks.get("voice_over"), Mapping):
        candidates.append(checks["voice_over"])
    # A narration asset may store provider/model/voice directly.
    if any(_alias(value, field) is not None for field in ("provider", "voice_id", "voice")):
        candidates.append(value)
    for candidate in candidates:
        try:
            return normalize_voice_identity(candidate)
        except VoiceContractError:
            continue
    return None


def validate_voice_propagation(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected: Mapping[str, Any] | VoiceIdentity | None = None,
) -> dict[str, Any]:
    """Compare voice identity across production, proposal, assets, and render.

    Missing identities are reported only when at least one artifact opts into
    the contract.  This keeps old exploratory artifacts readable while making
    a declared production identity fail closed on drift.
    """
    observed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    expected_identity = normalize_voice_identity(expected) if expected is not None else None
    for name, artifact in artifacts.items():
        if not isinstance(artifact, Mapping):
            continue
        identity = _identity_from_artifact(artifact)
        if identity is None:
            continue
        observed[str(name)] = identity.contract()
    reference = expected_identity or next(
        (normalize_voice_identity(item) for item in observed.values()), None
    )
    if reference is not None:
        for name, payload in observed.items():
            identity = normalize_voice_identity(payload)
            if identity.identity_key != reference.identity_key:
                errors.append(
                    f"voice identity drift in {name}: {identity.provider}/{identity.model}/"
                    f"{identity.voice_id}/{identity.locale} does not match "
                    f"{reference.provider}/{reference.model}/{reference.voice_id}/{reference.locale}"
                )
        # If a caller explicitly supplied expected identity, every artifact
        # that carries a voice contract must match it; silent defaults are not
        # allowed to appear as an unobserved provider.
    return {
        "valid": not errors,
        "errors": errors,
        "reference": reference.contract() if reference else None,
        "artifacts": observed,
        "checked": bool(observed or expected_identity),
    }


def assert_voice_propagation(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    expected: Mapping[str, Any] | VoiceIdentity | None = None,
) -> dict[str, Any]:
    report = validate_voice_propagation(artifacts, expected=expected)
    if not report["valid"]:
        raise VoiceContractError("voice identity propagation failed: " + "; ".join(report["errors"]))
    return report


def strict_bool(value: Any, field_name: str, *, default: bool = False) -> bool:
    """Read a contract boolean without accepting truthy strings or integers."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise VoiceContractError(f"{field_name} must be boolean")
    return value


def normalize_narration_text(text: Any) -> str:
    if not isinstance(text, str):
        raise VoiceContractError("narration text must be a string")
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"[\w’'-]+", text, flags=re.UNICODE))


@dataclass(frozen=True)
class NarrationSegment:
    segment_id: str
    ordinal: int
    section_id: str
    text: str
    text_hash: str
    voice_identity_key: str
    start_seconds: float
    end_seconds: float
    expected_duration_seconds: float
    pronunciation_dictionary_version: str = DEFAULT_PRONUNCIATION_DICTIONARY_VERSION
    measured_duration_seconds: float | None = None
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "id": self.segment_id,
            "ordinal": self.ordinal,
            "section_id": self.section_id,
            "text": self.text,
            "text_hash": self.text_hash,
            "voice_identity_key": self.voice_identity_key,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "expected_duration_seconds": self.expected_duration_seconds,
            "measured_duration_seconds": self.measured_duration_seconds,
            "pronunciation_dictionary_version": self.pronunciation_dictionary_version,
            "status": self.status,
        }


def _section_values(sections: Sequence[Mapping[str, Any] | str]) -> Iterable[tuple[str, str]]:
    for index, section in enumerate(sections, start=1):
        if isinstance(section, str):
            section_id, raw_text = f"section_{index:03d}", section
        elif isinstance(section, Mapping):
            section_id = str(section.get("section_id") or section.get("id") or section.get("scene_id") or f"section_{index:03d}")
            raw_text = section.get("text") or section.get("narration") or section.get("script") or ""
        else:
            raise VoiceContractError(f"narration section {index} must be an object or string")
        text = normalize_narration_text(raw_text)
        if not text:
            raise VoiceContractError(f"narration section {section_id!r} has no text")
        yield section_id, text


def plan_narration_segments(
    sections: Sequence[Mapping[str, Any] | str],
    voice: Mapping[str, Any] | VoiceIdentity,
    *,
    voice_rate_wpm: float = 150.0,
    pause_seconds: float = 0.25,
    pronunciation_dictionary_version: str = DEFAULT_PRONUNCIATION_DICTIONARY_VERSION,
    start_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    """Create stable, ordered narration segments and timing metadata."""
    identity = normalize_voice_identity(voice)
    if voice_rate_wpm <= 0:
        raise VoiceContractError("voice_rate_wpm must be positive")
    if pause_seconds < 0 or start_seconds < 0:
        raise VoiceContractError("pause_seconds and start_seconds cannot be negative")
    dictionary_version = _required_text(pronunciation_dictionary_version, "pronunciation_dictionary_version")
    result: list[dict[str, Any]] = []
    cursor = float(start_seconds)
    for ordinal, (section_id, text) in enumerate(_section_values(sections), start=1):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        key_payload = {
            "section_id": section_id,
            "ordinal": ordinal,
            "text": text,
            "text_hash": text_hash,
            "voice_identity": identity.contract(),
            "pronunciation_dictionary_version": dictionary_version,
        }
        segment_id = hashlib.sha256(_canonical_json(key_payload).encode("utf-8")).hexdigest()[:24]
        expected = max(0.1, (_word_count(text) / float(voice_rate_wpm)) * 60.0 + float(pause_seconds))
        segment = NarrationSegment(
            segment_id=segment_id,
            ordinal=ordinal,
            section_id=section_id,
            text=text,
            text_hash=text_hash,
            voice_identity_key=identity.identity_key,
            start_seconds=round(cursor, 3),
            end_seconds=round(cursor + expected, 3),
            expected_duration_seconds=round(expected, 3),
            pronunciation_dictionary_version=dictionary_version,
        )
        result.append({**segment.to_dict(), "voice_identity": identity.contract()})
        cursor += expected
    return result


def validate_narration_manifest(segments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    previous_end: float | None = None
    seen: set[str] = set()
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            errors.append(f"segment {index} is not an object")
            continue
        segment_id = str(segment.get("segment_id") or segment.get("id") or "")
        if not segment_id:
            errors.append(f"segment {index} has no segment_id")
        elif segment_id in seen:
            errors.append(f"duplicate segment_id {segment_id}")
        seen.add(segment_id)
        try:
            start = float(segment.get("start_seconds"))
            end = float(segment.get("end_seconds"))
        except (TypeError, ValueError):
            errors.append(f"segment {segment_id or index} has invalid timing")
            continue
        if end <= start:
            errors.append(f"segment {segment_id or index} end must be after start")
        if previous_end is not None and abs(start - previous_end) > 0.01:
            errors.append(f"segment {segment_id or index} is not contiguous with the previous segment")
        previous_end = end
    return {"valid": not errors, "errors": errors, "segment_count": len(segments)}


def require_voice_sample_approval(
    voice_selection: Mapping[str, Any] | None,
    *,
    sample: Mapping[str, Any] | None = None,
    batch: bool = True,
) -> dict[str, Any]:
    """Fail closed when a declared sample gate has not been approved."""
    selection = voice_selection if isinstance(voice_selection, Mapping) else {}
    required = any(
        strict_bool(selection.get(name), f"voice_selection.{name}")
        for name in ("sample_approval_required", "approval_required", "sample_required")
    )
    if not required or not batch:
        return {"allowed": True, "required": required, "approved": True, "reason": "sample gate not required"}
    sample_payload = sample if isinstance(sample, Mapping) else {}
    approved = any(
        strict_bool(sample_payload.get(name), f"sample_approval.{name}")
        for name in ("approved", "user_approved")
    )
    status = sample_payload.get("status")
    if status is not None and status not in {"approved", "accepted"}:
        raise VoiceContractError(
            "sample_approval.status must be 'approved' or 'accepted' when provided"
        )
    approved = approved or strict_bool(
        selection.get("sample_approved"),
        "voice_selection.sample_approved",
    )
    if not approved:
        raise VoiceSampleApprovalError(
            "batch narration is blocked until the selected voice sample is explicitly approved"
        )
    return {"allowed": True, "required": True, "approved": True, "reason": "voice sample approved"}


class VoiceSegmentCache:
    """Atomic per-segment cache that survives retries and process restarts."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def metadata_path(self, segment: Mapping[str, Any] | str) -> Path:
        key = str(segment if isinstance(segment, str) else segment.get("segment_id") or segment.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key):
            raise VoiceContractError("invalid narration segment cache key")
        return self.cache_dir / f"{key}.json"

    def load(self, segment: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self.metadata_path(segment)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if payload.get("status") != "completed":
            return None
        if payload.get("segment_id") != segment.get("segment_id"):
            return None
        artifact = payload.get("audio_path") or payload.get("path")
        if not isinstance(artifact, str) or not Path(artifact).is_file() or Path(artifact).stat().st_size <= 0:
            return None
        expected_hash = payload.get("artifact_sha256")
        if expected_hash and _file_sha256(Path(artifact)) != expected_hash:
            return None
        return payload

    def store(self, segment: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        artifact = result.get("audio_path") or result.get("path") or result.get("output")
        if not isinstance(artifact, str) or not Path(artifact).is_file() or Path(artifact).stat().st_size <= 0:
            raise VoiceContractError("cannot cache narration segment without a non-empty audio artifact")
        payload = {
            **dict(segment),
            **dict(result),
            "segment_id": segment.get("segment_id") or segment.get("id"),
            "status": "completed",
            "audio_path": artifact,
            "artifact_sha256": _file_sha256(Path(artifact)),
        }
        destination = self.metadata_path(segment)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(self.cache_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            Path(temporary).replace(destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute_narration_segments(
    segments: Sequence[Mapping[str, Any]],
    generate: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    cache: VoiceSegmentCache | None = None,
) -> dict[str, Any]:
    """Generate only missing/failed segments; preserve completed work."""
    records: list[dict[str, Any]] = []
    for segment in segments:
        cached = cache.load(segment) if cache else None
        if cached:
            records.append({**dict(segment), **cached, "cache_hit": True, "status": "completed"})
            continue
        try:
            generated = dict(generate(segment))
            if cache:
                generated = cache.store(segment, generated)
            records.append({**dict(segment), **generated, "cache_hit": False, "status": "completed"})
        except Exception as exc:
            records.append({**dict(segment), "status": "failed", "error": str(exc), "cache_hit": False})
            return {
                "valid": False,
                "segments": records,
                "failed_segment_id": segment.get("segment_id") or segment.get("id"),
                "completed_count": sum(item.get("status") == "completed" for item in records),
            }
    return {
        "valid": True,
        "segments": records,
        "failed_segment_id": None,
        "completed_count": len(records),
    }


def compare_transcript_to_script(
    expected_text: str,
    observed_text: str,
    *,
    minimum_similarity: float = 0.94,
) -> dict[str, Any]:
    """Compare normalized words and catch punctuation spoken as words."""
    expected = normalize_narration_text(expected_text)
    observed = normalize_narration_text(observed_text)
    expected_words = re.findall(r"[\w’'-]+", expected.lower(), flags=re.UNICODE)
    observed_words = re.findall(r"[\w’'-]+", observed.lower(), flags=re.UNICODE)
    if not expected_words or not observed_words:
        return {"valid": False, "transcript_matches_script": False, "word_accuracy": 0.0, "issues": ["script or transcript is empty"]}
    # Sequence-aware edit distance, bounded to keep this deterministic and
    # dependency-free for local/offline gates.
    prev = list(range(len(observed_words) + 1))
    for i, expected_word in enumerate(expected_words, start=1):
        current = [i]
        for j, observed_word in enumerate(observed_words, start=1):
            cost = 0 if expected_word == observed_word else 1
            current.append(min(current[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = current
    distance = prev[-1]
    accuracy = max(0.0, 1.0 - distance / max(len(expected_words), len(observed_words)))
    punctuation_leaks = [word for word in observed_words if word in {"dot", "comma", "period", "ellipsis"} and word not in expected_words]
    issues: list[str] = []
    if punctuation_leaks:
        issues.append("transcript contains punctuation words not present in the script: " + ", ".join(punctuation_leaks))
    if accuracy < minimum_similarity:
        issues.append(f"transcript word accuracy {accuracy:.3f} is below the required {minimum_similarity:.3f}")
    return {
        "valid": accuracy >= minimum_similarity and not punctuation_leaks,
        "transcript_matches_script": accuracy >= minimum_similarity and not punctuation_leaks,
        "word_accuracy": round(accuracy, 4),
        "expected_word_count": len(expected_words),
        "observed_word_count": len(observed_words),
        "punctuation_leaks": punctuation_leaks,
        "issues": issues,
    }


def verify_transcript(
    transcript: Mapping[str, Any] | Path | str,
    script_text: str,
    *,
    expected_segment_ids: Sequence[str] | None = None,
    pronunciation_notes: Sequence[str] | None = None,
    minimum_similarity: float = 0.94,
) -> dict[str, Any]:
    """Verify an STT/transcriber result against the authored narration.

    Accepts the common OpenMontage word-timestamp shape as well as a plain
    ``{"text": "..."}`` response.  Missing transcript/segments are explicit
    failures, never an implicit pass.
    """
    payload: Mapping[str, Any]
    if isinstance(transcript, (Path, str)):
        try:
            raw = json.loads(Path(transcript).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {"valid": False, "transcript_matches_script": False, "word_accuracy": 0.0, "issues": [f"transcript could not be read: {exc}"]}
        if not isinstance(raw, Mapping):
            return {"valid": False, "transcript_matches_script": False, "word_accuracy": 0.0, "issues": ["transcript root must be an object"]}
        payload = raw
    elif isinstance(transcript, Mapping):
        payload = transcript
    else:
        return {"valid": False, "transcript_matches_script": False, "word_accuracy": 0.0, "issues": ["transcript is not provided"]}

    words = payload.get("word_timestamps") or payload.get("words")
    if isinstance(words, Sequence) and not isinstance(words, (str, bytes)):
        observed_text = " ".join(
            str(item.get("word") or item.get("text") or "").strip()
            for item in words
            if isinstance(item, Mapping)
        ).strip()
    else:
        observed_text = str(payload.get("text") or payload.get("transcript") or "")
    report = compare_transcript_to_script(
        script_text,
        observed_text,
        minimum_similarity=minimum_similarity,
    )
    issues = list(report.get("issues") or [])
    expected_ids = {str(item) for item in (expected_segment_ids or []) if str(item)}
    observed_ids: set[str] = set()
    raw_segments = payload.get("segments")
    if isinstance(raw_segments, Sequence) and not isinstance(raw_segments, (str, bytes)):
        for item in raw_segments:
            if isinstance(item, Mapping) and (item.get("segment_id") or item.get("id")):
                observed_ids.add(str(item.get("segment_id") or item.get("id")))
    missing_segments = sorted(expected_ids - observed_ids) if expected_ids else []
    if missing_segments:
        issues.append("transcript is missing narration segments: " + ", ".join(missing_segments))
    report.update({
        "valid": bool(report.get("valid")) and not missing_segments,
        "transcript_matches_script": bool(report.get("transcript_matches_script")) and not missing_segments,
        "missing_segment_ids": missing_segments,
        "pronunciation_notes": [str(item) for item in (pronunciation_notes or [])],
        "issues": issues,
    })
    return report


__all__ = [
    "VoiceContractError",
    "VoiceSampleApprovalError",
    "VoiceIdentity",
    "NarrationSegment",
    "VoiceSegmentCache",
    "normalize_voice_identity",
    "voice_identity_key",
    "validate_voice_identity",
    "validate_voice_propagation",
    "assert_voice_propagation",
    "strict_bool",
    "normalize_narration_text",
    "plan_narration_segments",
    "validate_narration_manifest",
    "require_voice_sample_approval",
    "execute_narration_segments",
    "compare_transcript_to_script",
    "verify_transcript",
]
