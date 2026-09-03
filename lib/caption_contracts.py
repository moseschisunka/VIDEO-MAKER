"""Contracts for captions generated from the approved narration transcript."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
import textwrap
from typing import Any


class CaptionContractError(ValueError):
    """Raised when a caption transcript is unverified or temporally unsafe."""


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CaptionContractError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise CaptionContractError(f"{field} must be finite")
    if result < 0:
        raise CaptionContractError(f"{field} cannot be negative")
    return result


# ---------------------------------------------------------------------------
# Runtime-neutral caption rendering contract
# ---------------------------------------------------------------------------

DEFAULT_CAPTION_SAFE_AREA: dict[str, float] = {
    "left_ratio": 0.05,
    "right_ratio": 0.05,
    "top_ratio": 0.05,
    "bottom_ratio": 0.12,
}


def _safe_area(value: Mapping[str, Any] | None) -> dict[str, float]:
    """Normalize safe-area ratios and reject values that can hide captions."""

    raw = dict(DEFAULT_CAPTION_SAFE_AREA)
    if isinstance(value, Mapping):
        aliases = {"left": "left_ratio", "right": "right_ratio", "top": "top_ratio", "bottom": "bottom_ratio"}
        for key, item in value.items():
            target = aliases.get(str(key), str(key))
            if target in raw:
                try:
                    number = float(item)
                except (TypeError, ValueError) as exc:
                    raise CaptionContractError(f"safe area {target} must be numeric") from exc
                if not math.isfinite(number) or number < 0 or number >= 0.5:
                    raise CaptionContractError(f"safe area {target} must be finite and between 0 and 0.5")
                raw[target] = number
    if raw["left_ratio"] + raw["right_ratio"] >= 0.8:
        raise CaptionContractError("caption safe area leaves insufficient horizontal width")
    if raw["top_ratio"] + raw["bottom_ratio"] >= 0.8:
        raise CaptionContractError("caption safe area leaves insufficient vertical height")
    return raw


def _caption_lines(text: str, max_chars_per_line: int) -> list[str]:
    """Wrap on word boundaries without silently splitting long words."""

    if max_chars_per_line < 1:
        raise CaptionContractError("max_chars_per_line must be positive")
    if not text.strip():
        return []
    lines: list[str] = []
    for paragraph in re.split(r"\r?\n", text.strip()):
        normalized = " ".join(paragraph.split())
        if not normalized:
            continue
        lines.extend(
            textwrap.wrap(
                normalized,
                width=max_chars_per_line,
                break_long_words=False,
                break_on_hyphens=False,
                replace_whitespace=True,
            )
            or [normalized]
        )
    return lines


def _language(value: Any) -> str:
    text = str(value or "").strip().replace("_", "-").lower()
    if not text or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", text):
        raise CaptionContractError("verified transcript language is required (for example en-US)")
    return text


def _words_from_segments(segments: Sequence[Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for seg_index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise CaptionContractError(f"transcript segment {seg_index} must be an object")
        raw_words = segment.get("words")
        if isinstance(raw_words, Sequence) and not isinstance(raw_words, (str, bytes)) and raw_words:
            for word_index, word in enumerate(raw_words):
                if not isinstance(word, Mapping):
                    raise CaptionContractError(f"transcript word {seg_index}:{word_index} must be an object")
                text = str(word.get("word") or word.get("text") or "").strip()
                if not text:
                    raise CaptionContractError(f"transcript word {seg_index}:{word_index} has no text")
                words.append(
                    {
                        "word": text,
                        "start": _number(word.get("start"), f"transcript word {seg_index}:{word_index}.start"),
                        "end": _number(word.get("end"), f"transcript word {seg_index}:{word_index}.end"),
                    }
                )
        else:
            text = str(segment.get("text") or "").strip()
            if not text:
                raise CaptionContractError(f"transcript segment {seg_index} has no text")
            words.append(
                {
                    "word": text,
                    "start": _number(segment.get("start"), f"transcript segment {seg_index}.start"),
                    "end": _number(segment.get("end"), f"transcript segment {seg_index}.end"),
                }
            )
    return words


def transcript_digest(transcript: Mapping[str, Any]) -> str:
    """Return a stable digest of the verified transcript content/timing."""

    payload = {
        "language": transcript.get("language"),
        "segments": transcript.get("segments") or transcript.get("word_timestamps") or transcript.get("words") or [],
        "text": transcript.get("text") or transcript.get("transcript") or transcript.get("full_text") or "",
        "source_audio_sha256": transcript.get("source_audio_sha256"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_verified_transcript(
    transcript: Mapping[str, Any] | None,
    *,
    expected_language: str | None = None,
    expected_text: str | None = None,
    minimum_similarity: float = 0.94,
) -> dict[str, Any]:
    """Validate approval, language, text, and word/segment timing.

    A transcript is not considered verified merely because it came from an
    STT provider.  Callers must carry an explicit ``verified: true`` or an
    approval status, and the timestamps must be monotonic and positive.
    """

    errors: list[str] = []
    if not isinstance(transcript, Mapping):
        return {"valid": False, "errors": ["verified transcript object is required"]}
    explicit_verified = transcript.get("verified")
    approved = (
        explicit_verified is True
        if explicit_verified is not None
        else str(transcript.get("verification_status") or transcript.get("status") or "").lower()
        in {"verified", "approved", "pass", "passed"}
    )
    if not approved:
        errors.append("caption generation requires an explicitly verified/approved transcript")
    try:
        language = _language(transcript.get("language") or transcript.get("locale"))
    except CaptionContractError as exc:
        errors.append(str(exc))
        language = ""
    if expected_language and language:
        expected = str(expected_language).strip().replace("_", "-").lower()
        if language != expected and language.split("-", 1)[0] != expected.split("-", 1)[0]:
            errors.append(f"transcript language {language!r} does not match expected {expected!r}")

    raw_segments = transcript.get("segments")
    if not isinstance(raw_segments, Sequence) or isinstance(raw_segments, (str, bytes)):
        raw_words = transcript.get("word_timestamps") or transcript.get("words")
        raw_segments = [{"words": raw_words}] if isinstance(raw_words, Sequence) else []
    if not raw_segments:
        errors.append("verified transcript has no segments or words")
        words: list[dict[str, Any]] = []
    else:
        try:
            words = _words_from_segments(raw_segments)
        except CaptionContractError as exc:
            errors.append(str(exc))
            words = []

    previous_end: float | None = None
    for index, word in enumerate(words):
        if word["end"] <= word["start"]:
            errors.append(f"transcript word {index} end must be after start")
        if previous_end is not None and word["start"] < previous_end - 0.01:
            errors.append(f"transcript word {index} overlaps the previous word")
        previous_end = max(previous_end or 0.0, word["end"])

    observed_text = " ".join(str(word["word"]) for word in words).strip()
    similarity_report: dict[str, Any] | None = None
    if expected_text is not None:
        from lib.voice_contracts import compare_transcript_to_script

        similarity_report = compare_transcript_to_script(
            expected_text, observed_text, minimum_similarity=minimum_similarity
        )
        if similarity_report.get("valid") is not True:
            errors.extend(str(item) for item in similarity_report.get("issues") or [])

    result: dict[str, Any] = {
        "valid": not errors,
        "verified": approved,
        "language": language or None,
        "word_count": len(words),
        "start_seconds": words[0]["start"] if words else None,
        "end_seconds": words[-1]["end"] if words else None,
        "transcript_digest": transcript_digest(transcript),
        "observed_text": observed_text,
        "errors": list(dict.fromkeys(errors)),
    }
    if similarity_report is not None:
        result["script_comparison"] = similarity_report
    return result


def normalize_caption_cues(
    cues: Sequence[Any],
    *,
    duration_seconds: float | None = None,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    safe_area: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate and normalize segment/phrase caption cues for every runtime.

    The function intentionally fails closed. A renderer must not wrap by
    truncating text, let overlapping cues race, or place a cue outside the
    video timeline. Returned cues have canonical ``start``, ``end``, ``text``,
    ``lines``, and ``index`` fields.
    """

    if not isinstance(cues, Sequence) or isinstance(cues, (str, bytes)):
        raise CaptionContractError("caption cues must be an array")
    try:
        max_lines_int = int(max_lines)
    except (TypeError, ValueError) as exc:
        raise CaptionContractError("max_lines must be an integer") from exc
    if max_lines_int < 1 or max_lines_int > 4:
        raise CaptionContractError("max_lines must be between 1 and 4")
    try:
        max_chars_int = int(max_chars_per_line)
    except (TypeError, ValueError) as exc:
        raise CaptionContractError("max_chars_per_line must be an integer") from exc
    if max_chars_int < 1 or max_chars_int > 200:
        raise CaptionContractError("max_chars_per_line must be between 1 and 200")
    duration = None if duration_seconds is None else _number(duration_seconds, "duration_seconds")
    _safe_area(safe_area)

    normalized: list[dict[str, Any]] = []
    previous_end: float | None = None
    for index, cue in enumerate(cues, start=1):
        if not isinstance(cue, Mapping):
            raise CaptionContractError(f"caption cue {index} must be an object")
        start = _number(cue.get("start", cue.get("start_seconds")), f"caption cue {index}.start")
        end = _number(cue.get("end", cue.get("end_seconds")), f"caption cue {index}.end")
        if end <= start:
            raise CaptionContractError(f"caption cue {index} end must be after start")
        if previous_end is not None and start < previous_end - 0.01:
            raise CaptionContractError(f"caption cue {index} overlaps the previous cue")
        if duration is not None and end > duration + 0.05:
            raise CaptionContractError(
                f"caption cue {index} ends at {end:.3f}s beyond video duration {duration:.3f}s"
            )
        text = str(cue.get("text") or cue.get("caption") or "").strip()
        if not text:
            raw_words = cue.get("words")
            if isinstance(raw_words, Sequence) and not isinstance(raw_words, (str, bytes)):
                text = " ".join(
                    str(item.get("word") or item.get("text") or "").strip()
                    for item in raw_words
                    if isinstance(item, Mapping)
                ).strip()
        if not text:
            raise CaptionContractError(f"caption cue {index} has no text")
        lines = _caption_lines(text, max_chars_int)
        if len(lines) > max_lines_int:
            raise CaptionContractError(
                f"caption cue {index} requires {len(lines)} lines (maximum {max_lines_int})"
            )
        if any(len(line) > max_chars_int for line in lines):
            raise CaptionContractError(
                f"caption cue {index} contains a word longer than max_chars_per_line"
            )
        item: dict[str, Any] = {
            "index": int(cue.get("index", index) or index),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": "\n".join(lines),
            "lines": lines,
            "line_count": len(lines),
            "character_count": len(text),
        }
        raw_words = cue.get("words")
        if isinstance(raw_words, Sequence) and not isinstance(raw_words, (str, bytes)):
            item["words"] = [
                {
                    "word": str(word.get("word") or word.get("text") or "").strip(),
                    "start": round(_number(word.get("start"), f"caption cue {index} word.start"), 3),
                    "end": round(_number(word.get("end"), f"caption cue {index} word.end"), 3),
                }
                for word in raw_words
                if isinstance(word, Mapping)
            ]
        normalized.append(item)
        previous_end = end
    return normalized


def _timestamp_seconds(value: str) -> float:
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise CaptionContractError(f"invalid caption timestamp {value!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4))
    if minutes >= 60 or seconds >= 60:
        raise CaptionContractError(f"invalid caption timestamp {value!r}")
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_caption_text(content: str, *, fmt: str | None = None) -> list[dict[str, Any]]:
    """Parse SRT, WebVTT, or SubtitleGen JSON into neutral caption cues."""

    text = str(content or "")
    if (fmt or "").lower() in {"json", "caption.json"} or text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CaptionContractError(f"invalid caption JSON: {exc}") from exc
        raw = payload.get("cues") if isinstance(payload, Mapping) else None
        if not isinstance(raw, Sequence):
            raise CaptionContractError("caption JSON must contain a cues array")
        return [dict(cue) for cue in raw if isinstance(cue, Mapping)]

    blocks = re.split(r"\r?\n\s*\r?\n", text.strip()) if text.strip() else []
    cues: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.splitlines()]
        if not lines:
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue  # WEBVTT header or non-caption metadata
        timing = lines[timing_index].split("-->", 1)
        if len(timing) != 2:
            raise CaptionContractError("caption timing line must contain -->")
        start = _timestamp_seconds(timing[0].strip().split()[0])
        end = _timestamp_seconds(timing[1].strip().split()[0])
        cue_text = " ".join(lines[timing_index + 1 :]).strip()
        cue_text = re.sub(r"<[^>]+>", "", cue_text).strip()
        if cue_text:
            cues.append({"index": len(cues) + 1, "start": start, "end": end, "text": cue_text})
    if not cues:
        raise CaptionContractError("caption file contains no parseable cues")
    return cues


def load_caption_cues(path: str | Path) -> list[dict[str, Any]]:
    """Load a supported caption sidecar with an actionable error."""

    source = Path(path)
    if not source.is_file():
        raise CaptionContractError(f"caption file not found: {source}")
    try:
        content = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CaptionContractError(f"could not read caption file {source}: {exc}") from exc
    suffix = source.suffix.lower()
    fmt = "json" if suffix == ".json" or source.name.lower().endswith(".caption.json") else suffix.lstrip(".")
    return parse_caption_text(content, fmt=fmt)


def cues_from_transcript(
    transcript: Mapping[str, Any], *, max_words_per_cue: int = 6, max_chars_per_line: int = 42
) -> list[dict[str, Any]]:
    """Build phrase cues from the already-validated transcript words."""

    segments = transcript.get("segments") or transcript.get("word_timestamps") or transcript.get("words") or []
    if isinstance(segments, Sequence) and not isinstance(segments, (str, bytes)):
        words = _words_from_segments(segments)
    else:
        words = []
    if not words:
        raise CaptionContractError("verified transcript has no words for caption rendering")
    try:
        limit = int(max_words_per_cue)
    except (TypeError, ValueError) as exc:
        raise CaptionContractError("max_words_per_cue must be an integer") from exc
    if limit < 1 or limit > 20:
        raise CaptionContractError("max_words_per_cue must be between 1 and 20")
    cues: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []
    text = ""
    for word in words:
        candidate = f"{text} {word['word']}".strip()
        if bucket and (len(bucket) >= limit or len(candidate) > int(max_chars_per_line)):
            cues.append({"index": len(cues) + 1, "start": bucket[0]["start"], "end": bucket[-1]["end"], "text": text, "words": list(bucket)})
            bucket = []
            text = ""
        bucket.append(word)
        text = f"{text} {word['word']}".strip() if text else word["word"]
    if bucket:
        cues.append({"index": len(cues) + 1, "start": bucket[0]["start"], "end": bucket[-1]["end"], "text": text, "words": list(bucket)})
    return cues


def caption_cues_digest(cues: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(list(cues), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rgb_color(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """Parse common CSS/ASS colors for a deterministic contrast check."""

    text = str(value or "").strip()
    if not text:
        return default
    if text.startswith("#"):
        raw = text[1:]
        if len(raw) in {3, 4}:
            raw = "".join(char * 2 for char in raw[:3])
        if len(raw) >= 6:
            try:
                return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
            except ValueError:
                return default
    rgba = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*[\d.]+)?\s*\)", text, flags=re.IGNORECASE)
    if rgba:
        return tuple(max(0, min(255, int(float(rgba.group(i))))) for i in (1, 2, 3))  # type: ignore[return-value]
    # ASS &HAABBGGRR stores channels in reverse order. Ignore alpha for the
    # contrast baseline; the renderer supplies an opaque/controlled backdrop.
    if text.upper().startswith("&H"):
        raw = text[2:].replace("&", "")
        if len(raw) >= 6:
            try:
                return int(raw[-2:], 16), int(raw[-4:-2], 16), int(raw[-6:-4], 16)
            except ValueError:
                return default
    return default


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    values = []
    for channel in rgb:
        linear = channel / 255
        values.append(linear / 12.92 if linear <= 0.03928 else ((linear + 0.055) / 1.055) ** 2.4)
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]


def caption_accessibility_report(
    *,
    cues: Sequence[Mapping[str, Any]],
    width: int,
    height: int,
    font_size: int,
    max_lines: int,
    max_chars_per_line: int,
    safe_area: Mapping[str, Any],
    style: Mapping[str, Any] | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Evaluate measurable caption readability and platform policy."""

    style_data = dict(style or {})
    profile_policy: dict[str, Any] = {}
    if profile_name:
        try:
            from lib.media_profiles import get_profile

            profile = get_profile(profile_name)
            profile_policy = {
                "max_lines": profile.caption_max_lines,
                "max_chars_per_line": profile.caption_max_chars_per_line,
                "min_font_size": profile.caption_min_font_size,
                "max_chars_per_second": profile.caption_max_chars_per_second,
                "safe_area_bottom_ratio": profile.caption_safe_area_bottom_ratio,
            }
        except (ImportError, ValueError):
            profile_policy = {}
    issues: list[str] = []
    policy_max_lines = int(profile_policy.get("max_lines", max_lines))
    policy_max_chars = int(profile_policy.get("max_chars_per_line", max_chars_per_line))
    min_font = int(profile_policy.get("min_font_size", max(16, round(int(height) * 0.018))))
    max_cps = float(profile_policy.get("max_chars_per_second", 22.0))
    if int(max_lines) > policy_max_lines:
        issues.append(f"caption max_lines {int(max_lines)} exceeds profile policy {policy_max_lines}")
    if int(max_chars_per_line) > policy_max_chars:
        issues.append(f"caption max_chars_per_line {int(max_chars_per_line)} exceeds profile policy {policy_max_chars}")
    if int(font_size) < min_font:
        issues.append(f"caption font_size {int(font_size)} is below readable minimum {min_font}")
    observed_cps = 0.0
    for cue in cues:
        seconds = max(0.001, float(cue["end"]) - float(cue["start"]))
        cps = len(str(cue.get("text") or "").replace("\n", " ").strip()) / seconds
        observed_cps = max(observed_cps, cps)
    if observed_cps > max_cps:
        issues.append(f"caption reading speed {observed_cps:.1f} characters/s exceeds {max_cps:.1f}")

    foreground = _rgb_color(
        style_data.get("primary_color") or style_data.get("color") or style_data.get("foreground_color"),
        (255, 255, 255),
    )
    background = _rgb_color(
        style_data.get("back_color") or style_data.get("background_color") or style_data.get("background"),
        (0, 0, 0),
    )
    contrast = (max(_relative_luminance(foreground), _relative_luminance(background)) + 0.05) / (
        min(_relative_luminance(foreground), _relative_luminance(background)) + 0.05
    )
    target = 3.0 if int(font_size) >= 24 else 4.5
    if contrast < target:
        issues.append(f"caption contrast ratio {contrast:.2f} is below {target:.1f}")
    safe_bottom = float(safe_area.get("bottom_ratio", DEFAULT_CAPTION_SAFE_AREA["bottom_ratio"]))
    if profile_policy and safe_bottom < float(profile_policy["safe_area_bottom_ratio"]):
        issues.append(
            f"caption bottom safe area {safe_bottom:.3f} is below profile minimum "
            f"{float(profile_policy['safe_area_bottom_ratio']):.3f}"
        )
    return {
        "valid": not issues,
        "profile": profile_name,
        "font_size": int(font_size),
        "minimum_font_size": min_font,
        "max_lines": int(max_lines),
        "max_chars_per_line": int(max_chars_per_line),
        "max_chars_per_second": max_cps,
        "maximum_observed_chars_per_second": round(observed_cps, 3),
        "foreground_rgb": foreground,
        "background_rgb": background,
        "contrast_ratio": round(contrast, 3),
        "contrast_target": target,
        "issues": issues,
    }


def build_caption_render_contract(
    *,
    runtime: str,
    mode: str,
    cues: Sequence[Any],
    width: int,
    height: int,
    duration_seconds: float,
    fps: int = 30,
    safe_area: Mapping[str, Any] | None = None,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    font_size: int = 42,
    style: Mapping[str, Any] | None = None,
    transcript_verification: Mapping[str, Any] | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Create the evidence object consumed by Remotion/HyperFrames/FFmpeg."""

    runtime_name = str(runtime or "").strip().lower()
    if runtime_name not in {"remotion", "hyperframes", "ffmpeg"}:
        raise CaptionContractError(f"unsupported caption runtime {runtime!r}")
    mode_name = str(mode or "").strip().lower()
    if mode_name not in {"burn_in", "sidecar", "overlay"}:
        raise CaptionContractError(f"unsupported caption render mode {mode!r}")
    try:
        output_width = int(width)
        output_height = int(height)
        output_fps = int(fps)
        output_font_size = int(font_size)
    except (TypeError, ValueError) as exc:
        raise CaptionContractError("caption dimensions, fps, and font_size must be integers") from exc
    if output_width < 1 or output_height < 1 or output_fps < 1:
        raise CaptionContractError("caption output dimensions and fps must be positive")
    if output_font_size < 8 or output_font_size > 240:
        raise CaptionContractError("caption font_size must be between 8 and 240")
    duration = _number(duration_seconds, "duration_seconds")
    normalized_safe = _safe_area(safe_area)
    normalized = normalize_caption_cues(
        cues,
        duration_seconds=duration,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        safe_area=normalized_safe,
    )
    if not normalized:
        raise CaptionContractError("caption render contract requires at least one cue")
    accessibility = caption_accessibility_report(
        cues=normalized,
        width=output_width,
        height=output_height,
        font_size=output_font_size,
        max_lines=int(max_lines),
        max_chars_per_line=int(max_chars_per_line),
        safe_area=normalized_safe,
        style=style,
        profile_name=profile_name,
    )
    if not accessibility["valid"]:
        raise CaptionContractError("; ".join(accessibility["issues"]))
    safe_pixels = {
        "left": round(output_width * normalized_safe["left_ratio"]),
        "right": round(output_width * normalized_safe["right_ratio"]),
        "top": round(output_height * normalized_safe["top_ratio"]),
        "bottom": round(output_height * normalized_safe["bottom_ratio"]),
    }
    return {
        "contract_version": "1.0",
        "runtime": runtime_name,
        "mode": mode_name,
        "width": output_width,
        "height": output_height,
        "fps": output_fps,
        "duration_seconds": round(duration, 3),
        "safe_area": {**normalized_safe, "pixels": safe_pixels},
        "wrapping": {"max_chars_per_line": int(max_chars_per_line), "max_lines": int(max_lines), "font_size": output_font_size},
        "style": dict(style or {}),
        "profile": profile_name,
        "accessibility": accessibility,
        "cue_count": len(normalized),
        "first_start_seconds": normalized[0]["start"],
        "last_end_seconds": normalized[-1]["end"],
        "cue_digest": caption_cues_digest(normalized),
        "cues": normalized,
        "transcript_verification": dict(transcript_verification or {}) or None,
        "valid": True,
    }


__all__ = [
    "CaptionContractError",
    "DEFAULT_CAPTION_SAFE_AREA",
    "build_caption_render_contract",
    "caption_cues_digest",
    "caption_accessibility_report",
    "cues_from_transcript",
    "load_caption_cues",
    "normalize_caption_cues",
    "parse_caption_text",
    "transcript_digest",
    "validate_verified_transcript",
]
