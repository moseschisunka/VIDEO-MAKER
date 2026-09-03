"""Editorial timeline quality gates for frame-accurate video authoring.

The functions in this module deal with *editorial visual beats*, not codec
frames.  A beat is a purposeful change of image, diagram state, camera move,
or scene component.  The iLearnZed production profile uses a two-second beat
cadence by default, which means a 30-second video needs at least 15 beats.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from lib.media_profiles import duration_tolerance_seconds as profile_duration_tolerance


DEFAULT_VISUAL_BEAT_SECONDS = 2.0
TIMELINE_EPSILON_SECONDS = 0.05


@dataclass(frozen=True)
class DurationPlan:
    """Canonical narration/visual timing plan for one output."""

    valid: bool
    target_duration_seconds: float
    planned_duration_seconds: float
    voice_rate_wpm: float
    word_count: int
    narration_seconds: float
    intro_seconds: float
    outro_seconds: float
    silence_seconds: float
    transition_seconds: float
    transition_count: int
    scene_count: int
    scene_durations: tuple[float, ...]
    available_content_seconds: float
    visual_hold_seconds: float
    max_words: int
    tolerance_seconds: float
    duration_policy: str
    profile: str | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "target_duration_seconds": self.target_duration_seconds,
            "planned_duration_seconds": self.planned_duration_seconds,
            "voice_rate_wpm": self.voice_rate_wpm,
            "word_count": self.word_count,
            "narration_seconds": self.narration_seconds,
            "intro_seconds": self.intro_seconds,
            "outro_seconds": self.outro_seconds,
            "silence_seconds": self.silence_seconds,
            "transition_seconds": self.transition_seconds,
            "transition_count": self.transition_count,
            "scene_count": self.scene_count,
            "scene_durations": list(self.scene_durations),
            "available_content_seconds": self.available_content_seconds,
            "visual_hold_seconds": self.visual_hold_seconds,
            "max_words": self.max_words,
            "tolerance_seconds": self.tolerance_seconds,
            "duration_policy": self.duration_policy,
            "profile": self.profile,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def count_script_words(text: str | None) -> int:
    """Count spoken words consistently across script and TTS planning."""

    if not text:
        return 0
    return len(re.findall(r"\b[\w’'-]+\b", str(text), flags=re.UNICODE))


def words_for_duration(
    duration_seconds: float,
    voice_rate_wpm: float = 150.0,
    *,
    overhead_seconds: float = 0.0,
) -> int:
    """Return the maximum narration words that fit a duration budget."""

    duration = float(duration_seconds)
    rate = float(voice_rate_wpm)
    overhead = float(overhead_seconds)
    if duration <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    if rate <= 0:
        raise ValueError("voice_rate_wpm must be greater than zero")
    if overhead < 0 or overhead >= duration:
        return 0
    return max(0, int(math.floor((duration - overhead) * rate / 60.0)))


def _round_scene_durations(total: float, scene_count: int) -> tuple[float, ...]:
    if scene_count <= 0:
        return ()
    base = round(total / scene_count, 3)
    values = [base for _ in range(scene_count)]
    # Keep the sum exactly equal to the planned duration after decimal
    # serialisation; the final scene absorbs rounding residue.
    values[-1] = round(total - sum(values[:-1]), 3)
    return tuple(values)


def solve_duration_plan(
    target_duration_seconds: float,
    *,
    word_count: int | None = None,
    script_text: str | None = None,
    voice_rate_wpm: float = 150.0,
    scene_count: int = 1,
    transition_duration_seconds: float = 0.0,
    transition_count: int | None = None,
    intro_seconds: float = 0.0,
    outro_seconds: float = 0.0,
    silence_seconds: float = 0.0,
    profile: str | None = None,
    duration_policy: str = "target_led",
    tolerance_seconds: float | None = None,
    minimum_scene_seconds: float = 0.25,
) -> dict[str, Any]:
    """Solve a target duration without speeding up or hiding narration.

    ``target_led`` keeps the rendered duration at the requested target (or
    within the canonical tolerance). ``content_led`` treats the target as a
    floor and grows only when the authored narration and declared breathing
    room require it.  Both modes fail when content exceeds a profile limit.
    """

    errors: list[str] = []
    warnings: list[str] = []
    try:
        target = float(target_duration_seconds)
    except (TypeError, ValueError):
        target = 0.0
    try:
        rate = float(voice_rate_wpm)
    except (TypeError, ValueError):
        rate = 0.0
    try:
        scenes = int(scene_count)
    except (TypeError, ValueError):
        scenes = 0
    try:
        transition_duration = float(transition_duration_seconds)
        intro = float(intro_seconds)
        outro = float(outro_seconds)
        silence = float(silence_seconds)
        minimum_scene = float(minimum_scene_seconds)
    except (TypeError, ValueError):
        transition_duration = intro = outro = silence = minimum_scene = -1.0

    if target <= 0:
        errors.append("target_duration_seconds must be greater than zero")
    if rate <= 0:
        errors.append("voice_rate_wpm must be greater than zero")
    if scenes < 1:
        errors.append("scene_count must be at least one")
    if transition_duration < 0:
        errors.append("transition_duration_seconds must not be negative")
    if transition_count is None:
        transitions = max(0, scenes - 1)
    else:
        try:
            transitions = int(transition_count)
        except (TypeError, ValueError):
            transitions = -1
    if transitions < 0:
        errors.append("transition_count must not be negative")
    for label, value in (("intro_seconds", intro), ("outro_seconds", outro), ("silence_seconds", silence)):
        if value < 0:
            errors.append(f"{label} must not be negative")
    if minimum_scene < 0:
        errors.append("minimum_scene_seconds must not be negative")
    if duration_policy not in {"target_led", "content_led"}:
        errors.append("duration_policy must be 'target_led' or 'content_led'")

    if word_count is not None:
        try:
            words = int(word_count)
        except (TypeError, ValueError):
            words = 0
            errors.append("word_count must be an integer")
    else:
        words = count_script_words(script_text)
    if words < 0:
        errors.append("word_count must not be negative")
        words = 0
    narration = (words / rate * 60.0) if rate > 0 else 0.0
    transition_total = max(0.0, transition_duration * max(0, transitions))
    overhead = max(0.0, intro) + max(0.0, outro) + max(0.0, silence) + transition_total
    tolerance = (
        profile_duration_tolerance(
            target,
            explicit_tolerance_seconds=tolerance_seconds,
        )
        if target > 0
        else 0.0
    )
    required = narration + overhead
    if duration_policy == "content_led":
        planned = max(target, required)
    else:
        planned = max(target, required) if required <= target + tolerance else target

    if duration_policy == "target_led" and required > target + tolerance:
        errors.append(
            f"narration and timing overhead require {required:.3f}s, beyond target "
            f"{target:.3f}s plus tolerance {tolerance:.3f}s; shorten the script or revise the target"
        )
    if duration_policy == "content_led" and required > target:
        warnings.append(
            f"content-led plan grows from {target:.3f}s to {required:.3f}s; record the duration decision"
        )
    if scenes > 0 and planned > 0 and planned / scenes < minimum_scene:
        errors.append(
            f"{scenes} scenes at {planned / scenes:.3f}s each are below the "
            f"minimum scene hold of {minimum_scene:.3f}s"
        )
    if profile:
        try:
            from lib.media_profiles import validate_duration

            validate_duration(profile, planned)
        except (ImportError, ValueError) as exc:
            errors.append(str(exc))

    available = max(0.0, planned - overhead)
    hold = max(0.0, planned - required)
    max_words = words_for_duration(target, rate, overhead_seconds=overhead) if target > 0 and rate > 0 else 0
    plan = DurationPlan(
        valid=not errors,
        target_duration_seconds=round(target, 3),
        planned_duration_seconds=round(planned, 3),
        voice_rate_wpm=round(rate, 3),
        word_count=words,
        narration_seconds=round(narration, 3),
        intro_seconds=round(max(0.0, intro), 3),
        outro_seconds=round(max(0.0, outro), 3),
        silence_seconds=round(max(0.0, silence), 3),
        transition_seconds=round(transition_total, 3),
        transition_count=max(0, transitions),
        scene_count=max(0, scenes),
        scene_durations=_round_scene_durations(planned, scenes) if planned > 0 and scenes > 0 else (),
        available_content_seconds=round(available, 3),
        visual_hold_seconds=round(hold, 3),
        max_words=max_words,
        tolerance_seconds=round(tolerance, 3),
        duration_policy=duration_policy,
        profile=profile,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return plan.as_dict()


def solve_duration(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Short alias for :func:`solve_duration_plan`."""

    return solve_duration_plan(*args, **kwargs)


def validate_duration_tolerance(
    actual_duration_seconds: float,
    target_duration_seconds: float,
    *,
    long_form: bool | None = None,
    tolerance_seconds: float | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Check a measured render against the canonical duration contract."""

    errors: list[str] = []
    try:
        actual = float(actual_duration_seconds)
        target = float(target_duration_seconds)
    except (TypeError, ValueError):
        return {
            "valid": False,
            "actual_duration_seconds": actual_duration_seconds,
            "target_duration_seconds": target_duration_seconds,
            "tolerance_seconds": 0.0,
            "delta_seconds": None,
            "errors": ["actual and target durations must be numeric"],
            "warnings": [],
        }
    if target <= 0:
        errors.append("target_duration_seconds must be greater than zero")
    if actual <= 0:
        errors.append("actual_duration_seconds must be greater than zero")
    tolerance = (
        profile_duration_tolerance(
            target,
            long_form=long_form,
            explicit_tolerance_seconds=tolerance_seconds,
        )
        if target > 0
        else 0.0
    )
    delta = actual - target
    if target > 0 and abs(delta) > tolerance:
        errors.append(
            f"measured duration {actual:.3f}s differs from target {target:.3f}s "
            f"by {delta:+.3f}s, outside ±{tolerance:.3f}s"
        )
    if profile:
        try:
            from lib.media_profiles import validate_duration

            validate_duration(profile, actual)
        except (ImportError, ValueError) as exc:
            errors.append(str(exc))
    return {
        "valid": not errors,
        "actual_duration_seconds": round(actual, 3),
        "target_duration_seconds": round(target, 3),
        "tolerance_seconds": round(tolerance, 3),
        "delta_seconds": round(delta, 3),
        "errors": list(dict.fromkeys(errors)),
        "warnings": [],
    }


def validate_script_duration(
    script: Mapping[str, Any],
    *,
    target_duration_seconds: float | None = None,
    voice_rate_wpm: float = 150.0,
    profile: str | None = None,
    actual_audio_duration_seconds: float | None = None,
    duration_policy: str = "target_led",
    tolerance_seconds: float | None = None,
) -> dict[str, Any]:
    """Validate script word budget and optional measured narration duration."""

    value = script if isinstance(script, Mapping) else {}
    sections = value.get("sections") or []
    texts: list[str] = []
    if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)):
        for section in sections:
            if isinstance(section, Mapping):
                texts.append(str(section.get("text") or ""))
    elif value.get("text"):
        texts.append(str(value.get("text")))
    text = " ".join(texts)
    target = target_duration_seconds
    if target is None:
        target = value.get("target_duration_seconds") or value.get("total_duration_seconds")
    if target is None:
        return {
            "valid": False,
            "errors": ["script must declare target_duration_seconds or total_duration_seconds"],
            "warnings": [],
        }
    plan = solve_duration_plan(
        float(target),
        word_count=count_script_words(text),
        voice_rate_wpm=voice_rate_wpm,
        scene_count=max(1, len(sections) if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)) else 1),
        profile=profile,
        duration_policy=duration_policy,
        tolerance_seconds=tolerance_seconds,
    )
    errors = list(plan["errors"])
    warnings = list(plan["warnings"])
    declared = value.get("total_duration_seconds")
    if declared is not None:
        measured_declared = validate_duration_tolerance(
            float(declared),
            float(target),
            tolerance_seconds=tolerance_seconds,
            profile=profile,
        )
        if not measured_declared["valid"]:
            errors.extend(measured_declared["errors"])
    audio_report = None
    if actual_audio_duration_seconds is not None:
        audio_report = validate_duration_tolerance(
            actual_audio_duration_seconds,
            float(target),
            tolerance_seconds=tolerance_seconds,
            profile=profile,
        )
        if not audio_report["valid"]:
            errors.extend(audio_report["errors"])
    return {
        "valid": not errors,
        "word_count": count_script_words(text),
        "target_duration_seconds": float(target),
        "plan": plan,
        "audio_report": audio_report,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def validate_timeline_contract(
    cuts: list[dict[str, Any]],
    narration: Mapping[str, Any] | None,
    *,
    target_duration_seconds: float,
    measured_duration_seconds: float | None = None,
    duration_plan: Mapping[str, Any] | None = None,
    profile: str | None = None,
    long_form: bool | None = None,
) -> dict[str, Any]:
    """Combine visual, narration, and measured-duration checks.

    This is the single integration entry point used by compose/review gates;
    individual validators remain available for focused diagnostics.
    """

    plan = dict(duration_plan or {})
    minimum_beats = plan.get("scene_count")
    visual = validate_visual_timeline(
        cuts,
        duration_seconds=target_duration_seconds,
        minimum_beats=(int(minimum_beats) if minimum_beats is not None else None),
    )
    narration_report = validate_narration_timeline(narration, float(target_duration_seconds))
    duration_report = None
    if measured_duration_seconds is not None:
        duration_report = validate_duration_tolerance(
            measured_duration_seconds,
            target_duration_seconds,
            long_form=long_form,
            profile=profile,
        )
    errors = list(visual.get("errors") or []) + list(narration_report.get("errors") or [])
    warnings = list(visual.get("warnings") or []) + list(narration_report.get("warnings") or [])
    if duration_report:
        errors.extend(duration_report.get("errors") or [])
        warnings.extend(duration_report.get("warnings") or [])
    if profile:
        try:
            from lib.media_profiles import validate_duration

            validate_duration(profile, float(target_duration_seconds))
        except (ImportError, ValueError) as exc:
            errors.append(str(exc))
    return {
        "valid": not errors,
        "target_duration_seconds": float(target_duration_seconds),
        "profile": profile,
        "duration_plan": plan or None,
        "visual": visual,
        "narration": narration_report,
        "duration": duration_report,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
    }


def minimum_visual_beats(
    duration_seconds: float,
    beat_seconds: float = DEFAULT_VISUAL_BEAT_SECONDS,
) -> int:
    """Return the minimum number of editorial beats for a duration."""

    duration = float(duration_seconds)
    cadence = float(beat_seconds)
    if duration <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    if cadence <= 0:
        raise ValueError("beat_seconds must be greater than zero")
    # A duration just over a cadence needs the next beat. Tolerance belongs to
    # timestamp comparisons, not to the user-facing minimum-count rule.
    return max(1, int(math.ceil(duration / cadence)))


def _number(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc


def _primary_cuts(cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the visual coverage track while ignoring overlay-only cuts."""

    primary = [cut for cut in cuts if cut.get("layer", "primary") == "primary"]
    return primary or list(cuts)


def validate_visual_timeline(
    cuts: list[dict[str, Any]],
    duration_seconds: float | None = None,
    beat_seconds: float = DEFAULT_VISUAL_BEAT_SECONDS,
    epsilon_seconds: float = TIMELINE_EPSILON_SECONDS,
    minimum_beats: int | None = None,
) -> dict[str, Any]:
    """Validate coverage, cadence, and transition bounds for a visual track.

    ``minimum_beats`` is an explicit content-led override.  It preserves the
    two-second cadence rule for short-form edits while allowing a narrated
    lesson to hold each slide longer when the explanation needs it.

    The returned report is deliberately serialisable so it can be included in
    render diagnostics and checkpoints.  It does not mutate the edit.
    """

    errors: list[str] = []
    warnings: list[str] = []
    cadence = float(beat_seconds)

    if cadence <= 0:
        return {
            "valid": False,
            "errors": ["Visual beat cadence must be greater than zero."],
            "warnings": [],
        }
    if not cuts:
        return {
            "valid": False,
            "errors": ["Visual timeline must contain at least one primary cut."],
            "warnings": [],
        }

    track = _primary_cuts(cuts)
    ordered: list[tuple[float, float, dict[str, Any]]] = []
    for index, cut in enumerate(track):
        try:
            start = _number(cut.get("in_seconds"), f"cut[{index}].in_seconds")
            end = _number(cut.get("out_seconds"), f"cut[{index}].out_seconds")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if start < -epsilon_seconds:
            errors.append(f"Cut {cut.get('id', index)!r} starts before zero.")
        if end <= start + epsilon_seconds:
            errors.append(
                f"Cut {cut.get('id', index)!r} has no usable duration ({start:g}-{end:g}s)."
            )
        ordered.append((start, end, cut))

    ordered.sort(key=lambda item: (item[0], item[1]))
    inferred_duration = max((end for _, end, _ in ordered), default=0.0)
    duration = (
        inferred_duration
        if duration_seconds is None
        else float(duration_seconds)
    )
    if duration <= 0:
        errors.append("Visual timeline duration must be greater than zero.")

    if ordered:
        first_start = ordered[0][0]
        last_end = max(end for _, end, _ in ordered)
        if first_start > epsilon_seconds:
            errors.append(
                f"Visual timeline starts at {first_start:g}s; the primary track must start at 0s."
            )
        if last_end < duration - epsilon_seconds:
            errors.append(
                f"Visual timeline ends at {last_end:g}s but the declared duration is {duration:g}s."
            )
        if last_end > duration + epsilon_seconds:
            errors.append(
                f"Visual timeline ends at {last_end:g}s beyond the declared duration of {duration:g}s."
            )

    transition_count = 0
    for index, (start, end, cut) in enumerate(ordered):
        beat_duration = max(0.0, end - start)
        transition_duration = cut.get("transition_duration")
        if transition_duration is not None:
            try:
                transition = _number(
                    transition_duration,
                    f"cut[{index}].transition_duration",
                )
                if transition < 0:
                    errors.append(f"Cut {cut.get('id', index)!r} has a negative transition duration.")
                if transition > beat_duration / 2 + epsilon_seconds:
                    errors.append(
                        f"Cut {cut.get('id', index)!r} transition is {transition:g}s, "
                        f"but its {beat_duration:g}s beat can safely use at most {beat_duration / 2:g}s."
                    )
            except ValueError as exc:
                errors.append(str(exc))
        if cut.get("transition_in") or cut.get("transition_out"):
            transition_count += 1

        if index == 0:
            continue
        previous_start, previous_end, previous_cut = ordered[index - 1]
        gap = start - previous_end
        if gap > epsilon_seconds:
            errors.append(
                f"Gap of {gap:g}s between cuts {previous_cut.get('id', index - 1)!r} "
                f"and {cut.get('id', index)!r}; visual coverage must be continuous."
            )
        elif gap < -epsilon_seconds:
            errors.append(
                f"Overlap of {-gap:g}s between cuts {previous_cut.get('id', index - 1)!r} "
                f"and {cut.get('id', index)!r}; use an explicit transition model instead."
            )

    required_beats = (
        max(1, int(minimum_beats))
        if minimum_beats is not None
        else (minimum_visual_beats(duration, cadence) if duration > 0 else 0)
    )
    if len(ordered) < required_beats:
        errors.append(
            f"Only {len(ordered)} visual beats cover {duration:g}s; "
            f"at least {required_beats} are required at a {cadence:g}s cadence."
        )
    if transition_count < max(0, len(ordered) - 1):
        warnings.append(
            f"Only {transition_count}/{max(0, len(ordered) - 1)} beat boundaries declare an explicit transition; "
            "the renderer will use its deterministic fade default for the remainder."
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "duration_seconds": round(duration, 3),
        "beat_seconds": cadence,
        "cut_count": len(ordered),
        "minimum_beats": required_beats,
        "transition_boundaries_declared": transition_count,
        "coverage_start_seconds": round(ordered[0][0], 3) if ordered else None,
        "coverage_end_seconds": round(max((end for _, end, _ in ordered), default=0.0), 3),
    }


def validate_narration_timeline(
    narration: dict[str, Any] | None,
    duration_seconds: float,
    epsilon_seconds: float = TIMELINE_EPSILON_SECONDS,
) -> dict[str, Any]:
    """Validate optional narration segment markers against the visual timeline."""

    segments = list((narration or {}).get("segments") or [])
    if not segments:
        return {
            "valid": True,
            "segment_count": 0,
            "coverage_verified": bool((narration or {}).get("src")),
            "errors": [],
            "warnings": [],
        }

    errors: list[str] = []
    warnings: list[str] = []
    parsed: list[tuple[float, float | None, dict[str, Any]]] = []
    for index, segment in enumerate(segments):
        try:
            start = _number(segment.get("start_seconds", 0), f"narration.segments[{index}].start_seconds")
            end_value = segment.get("end_seconds")
            end = None if end_value is None else _number(
                end_value,
                f"narration.segments[{index}].end_seconds",
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if start < -epsilon_seconds:
            errors.append(f"Narration segment {index} starts before zero.")
        if end is not None and end <= start + epsilon_seconds:
            errors.append(f"Narration segment {index} has no usable duration.")
        parsed.append((start, end, segment))

    parsed.sort(key=lambda item: item[0])
    for index in range(1, len(parsed)):
        previous_start, previous_end, _ = parsed[index - 1]
        current_start, _, _ = parsed[index]
        if previous_end is not None and current_start < previous_end - epsilon_seconds:
            errors.append(f"Narration segments {index - 1} and {index} overlap.")

    coverage_verified = False
    if parsed:
        first_start = parsed[0][0]
        last_end = parsed[-1][1]
        if first_start > epsilon_seconds:
            errors.append(f"Narration starts at {first_start:g}s instead of 0s.")
        if last_end is None:
            warnings.append(
                "Narration segment coverage cannot be proven because the final segment has no end_seconds."
            )
        elif last_end < duration_seconds - epsilon_seconds:
            errors.append(
                f"Narration ends at {last_end:g}s before the visual timeline ends at {duration_seconds:g}s."
            )
        else:
            coverage_verified = True

    return {
        "valid": not errors,
        "segment_count": len(parsed),
        "coverage_verified": coverage_verified,
        "errors": errors,
        "warnings": warnings,
    }


__all__ = [
    "DEFAULT_VISUAL_BEAT_SECONDS",
    "DurationPlan",
    "minimum_visual_beats",
    "count_script_words",
    "words_for_duration",
    "solve_duration_plan",
    "solve_duration",
    "validate_duration_tolerance",
    "validate_script_duration",
    "validate_timeline_contract",
    "validate_visual_timeline",
    "validate_narration_timeline",
]
