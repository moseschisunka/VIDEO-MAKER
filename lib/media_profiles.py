"""Internal media profile constants and render-profile helpers.

Defines platform-specific media profiles (resolution, aspect ratio, codec, etc.)
so the composer and publisher agents can format output correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AspectRatio(str, Enum):
    LANDSCAPE_16_9 = "16:9"
    PORTRAIT_9_16 = "9:16"
    SQUARE_1_1 = "1:1"
    CINEMATIC_21_9 = "21:9"
    STANDARD_4_3 = "4:3"


@dataclass(frozen=True)
class MediaProfile:
    """A named render profile for a target platform/format."""
    name: str
    width: int
    height: int
    aspect_ratio: AspectRatio
    fps: int
    codec: str
    audio_codec: str
    crf: int
    pixel_format: str = "yuv420p"
    max_file_size_mb: Optional[float] = None
    max_duration_seconds: Optional[float] = None
    caption_format: str = "srt"
    notes: str = ""
    # Audio quality contract.  LUFS is integrated loudness; true peak is the
    # maximum permitted dBTP (a negative value leaves codec headroom).
    audio_loudness_lufs: float = -16.0
    audio_loudness_tolerance_lufs: float = 1.5
    audio_true_peak_db: float = -1.0
    audio_silence_max_ratio: float = 0.98
    audio_clipping_max_samples: int = 0
    # Caption accessibility policy. These are profile facts, not renderer
    # guesses, so every runtime can certify against the same limits.
    caption_max_lines: int = 2
    caption_max_chars_per_line: int = 42
    caption_min_font_size: int = 24
    caption_max_chars_per_second: float = 22.0
    caption_safe_area_bottom_ratio: float = 0.12


# ---- Platform profiles ----

YOUTUBE_LANDSCAPE = MediaProfile(
    name="youtube_landscape",
    width=1920, height=1080,
    aspect_ratio=AspectRatio.LANDSCAPE_16_9,
    fps=30, codec="libx264", audio_codec="aac", crf=18,
    caption_format="srt",
    audio_loudness_lufs=-14.0,
    audio_loudness_tolerance_lufs=1.5,
    notes="YouTube standard HD upload",
)

ILEARNZED_LONG_FORM = MediaProfile(
    name="ilearnzed_long_form",
    width=1920, height=1080,
    aspect_ratio=AspectRatio.LANDSCAPE_16_9,
    fps=30, codec="libx264", audio_codec="aac", crf=18,
    caption_format="srt",
    notes="iLearnZed content-led long-form lesson or company video (no studio duration ceiling)",
)

YOUTUBE_4K = MediaProfile(
    name="youtube_4k",
    width=3840, height=2160,
    aspect_ratio=AspectRatio.LANDSCAPE_16_9,
    fps=30, codec="libx264", audio_codec="aac", crf=18,
    caption_format="srt",
    audio_loudness_lufs=-14.0,
    audio_loudness_tolerance_lufs=1.5,
    notes="YouTube 4K upload",
)

YOUTUBE_SHORTS = MediaProfile(
    name="youtube_shorts",
    width=1080, height=1920,
    aspect_ratio=AspectRatio.PORTRAIT_9_16,
    fps=30, codec="libx264", audio_codec="aac", crf=20,
    max_duration_seconds=60,
    caption_format="srt",
    audio_loudness_lufs=-14.0,
    audio_loudness_tolerance_lufs=1.5,
    notes="YouTube Shorts (max 60s, vertical)",
)

INSTAGRAM_REELS = MediaProfile(
    name="instagram_reels",
    width=1080, height=1920,
    aspect_ratio=AspectRatio.PORTRAIT_9_16,
    fps=30, codec="libx264", audio_codec="aac", crf=20,
    max_file_size_mb=250,
    max_duration_seconds=90,
    caption_format="srt",
    audio_loudness_lufs=-14.0,
    audio_loudness_tolerance_lufs=1.5,
    notes="Instagram Reels (max 90s, vertical)",
)

INSTAGRAM_FEED = MediaProfile(
    name="instagram_feed",
    width=1080, height=1080,
    aspect_ratio=AspectRatio.SQUARE_1_1,
    fps=30, codec="libx264", audio_codec="aac", crf=20,
    max_file_size_mb=250,
    max_duration_seconds=60,
    notes="Instagram feed video (square)",
)

TIKTOK = MediaProfile(
    name="tiktok",
    width=1080, height=1920,
    aspect_ratio=AspectRatio.PORTRAIT_9_16,
    fps=30, codec="libx264", audio_codec="aac", crf=20,
    max_file_size_mb=287,
    max_duration_seconds=600,
    caption_format="srt",
    audio_loudness_lufs=-14.0,
    audio_loudness_tolerance_lufs=1.5,
    notes="TikTok (max 10min, vertical preferred)",
)

LINKEDIN = MediaProfile(
    name="linkedin",
    width=1920, height=1080,
    aspect_ratio=AspectRatio.LANDSCAPE_16_9,
    fps=30, codec="libx264", audio_codec="aac", crf=20,
    max_file_size_mb=5120,
    max_duration_seconds=600,
    caption_format="srt",
    audio_loudness_lufs=-14.0,
    audio_loudness_tolerance_lufs=1.5,
    notes="LinkedIn video (landscape preferred, max 10min)",
)

CINEMATIC = MediaProfile(
    name="cinematic",
    width=2560, height=1080,
    aspect_ratio=AspectRatio.CINEMATIC_21_9,
    fps=24, codec="libx264", audio_codec="aac", crf=16,
    notes="Cinematic ultra-wide format",
)

GENERIC_HD = MediaProfile(
    name="generic_hd",
    width=1920, height=1080,
    aspect_ratio=AspectRatio.LANDSCAPE_16_9,
    fps=30, codec="libx264", audio_codec="aac", crf=23,
    caption_format="srt",
    notes="Generic HD output (no platform-specific constraints)",
)


# ---- Profile registry ----

ALL_PROFILES: dict[str, MediaProfile] = {
    p.name: p for p in [
        YOUTUBE_LANDSCAPE, ILEARNZED_LONG_FORM, YOUTUBE_4K, YOUTUBE_SHORTS,
        INSTAGRAM_REELS, INSTAGRAM_FEED,
        TIKTOK, LINKEDIN, CINEMATIC, GENERIC_HD,
    ]
}


SHORT_FORM_MAX_SECONDS = 60.0
SHORT_FORM_RELATIVE_TOLERANCE = 0.05
SHORT_FORM_MIN_TOLERANCE_SECONDS = 1.0
LONG_FORM_RELATIVE_TOLERANCE = 0.03


def get_profile(name: str) -> MediaProfile:
    """Get a media profile by name."""
    if name not in ALL_PROFILES:
        available = ", ".join(ALL_PROFILES.keys())
        raise ValueError(f"Unknown profile {name!r}. Available: {available}")
    return ALL_PROFILES[name]


def get_profiles_for_platform(platform: str) -> list[MediaProfile]:
    """Get all profiles matching a platform prefix."""
    return [p for name, p in ALL_PROFILES.items() if name.startswith(platform)]


def validate_duration(profile_name: str, duration_seconds: float) -> None:
    """Reject output durations that exceed a profile's hard platform limit."""
    profile = get_profile(profile_name)
    maximum = profile.max_duration_seconds
    if maximum is not None and duration_seconds > maximum:
        raise ValueError(
            f"Profile {profile_name!r} allows a maximum of {maximum:g} seconds; "
            f"requested duration is {duration_seconds:g} seconds."
        )


def duration_tolerance_seconds(
    duration_seconds: float,
    *,
    long_form: bool | None = None,
    explicit_tolerance_seconds: float | None = None,
) -> float:
    """Return the canonical editorial duration tolerance.

    Short products use the more permissive of ±5% and ±1 second.  Long-form
    products use ±3%, with no hidden one-second floor.  An explicit tolerance
    is reserved for a documented platform exception and is never negative.
    """

    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    if explicit_tolerance_seconds is not None:
        tolerance = float(explicit_tolerance_seconds)
        if tolerance < 0:
            raise ValueError("explicit_tolerance_seconds must not be negative")
        return tolerance
    is_long = duration > SHORT_FORM_MAX_SECONDS if long_form is None else bool(long_form)
    if is_long:
        return round(duration * LONG_FORM_RELATIVE_TOLERANCE, 3)
    return round(max(duration * SHORT_FORM_RELATIVE_TOLERANCE, SHORT_FORM_MIN_TOLERANCE_SECONDS), 3)


def profile_contract(profile_name: str) -> dict[str, object]:
    """Return the immutable output facts that must survive stage handoffs."""

    profile = get_profile(profile_name)
    return {
        "name": profile.name,
        "width": profile.width,
        "height": profile.height,
        "aspect_ratio": profile.aspect_ratio.value,
        "fps": profile.fps,
        "codec": profile.codec,
        "audio_codec": profile.audio_codec,
        "audio_loudness_lufs": profile.audio_loudness_lufs,
        "audio_loudness_tolerance_lufs": profile.audio_loudness_tolerance_lufs,
        "audio_true_peak_db": profile.audio_true_peak_db,
        "audio_silence_max_ratio": profile.audio_silence_max_ratio,
        "audio_clipping_max_samples": profile.audio_clipping_max_samples,
        "caption_max_lines": profile.caption_max_lines,
        "caption_max_chars_per_line": profile.caption_max_chars_per_line,
        "caption_min_font_size": profile.caption_min_font_size,
        "caption_max_chars_per_second": profile.caption_max_chars_per_second,
        "caption_safe_area_bottom_ratio": profile.caption_safe_area_bottom_ratio,
        "max_duration_seconds": profile.max_duration_seconds,
    }


def validate_profile_output(
    profile_name: str,
    *,
    width: int,
    height: int,
    aspect_ratio: str | None = None,
    fps: float | None = None,
    duration_seconds: float | None = None,
) -> dict[str, object]:
    """Validate probed output facts against a named profile.

    The returned report is serialisable so it can be carried in render
    evidence.  A mismatch is an error, not a best-effort warning.
    """

    profile = get_profile(profile_name)
    errors: list[str] = []
    if int(width) != profile.width or int(height) != profile.height:
        errors.append(
            f"dimensions {int(width)}x{int(height)} do not match {profile.name} "
            f"({profile.width}x{profile.height})"
        )
    if aspect_ratio is not None and str(aspect_ratio) != profile.aspect_ratio.value:
        errors.append(
            f"aspect_ratio {aspect_ratio!r} does not match {profile.name!r} "
            f"({profile.aspect_ratio.value})"
        )
    if fps is not None and abs(float(fps) - float(profile.fps)) > 0.5:
        errors.append(f"fps {fps!r} does not match {profile.name!r} ({profile.fps})")
    if duration_seconds is not None:
        try:
            validate_duration(profile.name, float(duration_seconds))
        except ValueError as exc:
            errors.append(str(exc))
    return {
        "valid": not errors,
        "profile": profile_contract(profile.name),
        "errors": errors,
    }


def ffmpeg_output_args(profile: MediaProfile) -> list[str]:
    """Generate FFmpeg output arguments for a media profile."""
    args = [
        "-c:v", profile.codec,
        "-c:a", profile.audio_codec,
        "-crf", str(profile.crf),
        "-pix_fmt", profile.pixel_format,
        "-r", str(profile.fps),
        "-vf", f"scale={profile.width}:{profile.height}",
    ]
    return args
