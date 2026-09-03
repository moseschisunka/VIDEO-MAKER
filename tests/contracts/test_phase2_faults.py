"""PR-208 concurrency and crash/fault contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lib.output_promotion import candidate_path, promote_candidate
from tests.contracts.test_phase2_output_promotion import _make_video


def _promote(candidate: Path, final: Path) -> dict:
    return promote_candidate(
        candidate,
        final,
        profile="youtube_landscape",
        expected_duration_seconds=1.0,
        run_started_at=datetime.now(timezone.utc) - timedelta(seconds=2),
        provenance={"run_id": "12345678-1234-4234-8234-123456789abc"},
    )


def test_two_projects_promote_in_parallel_without_cross_contamination(tmp_path: Path) -> None:
    candidates: list[Path] = []
    finals: list[Path] = []
    for index, duration in enumerate((1.0, 1.1), start=1):
        final = tmp_path / f"project-{index}" / "renders" / "final.mp4"
        candidate = candidate_path(tmp_path / f"project-{index}" / "run" / "candidates", final)
        # Distinct video bytes make accidental path/shared-workspace reuse
        # observable in the resulting hashes.
        _make_video(candidate, duration=duration)
        candidates.append(candidate)
        finals.append(final)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: _promote(*pair), zip(candidates, finals)))

    assert all(result["probe"]["width"] == 1920 for result in results)
    assert all(final.is_file() for final in finals)
    assert len({result["sha256"] for result in results}) == 2
    assert all(not candidate.exists() for candidate in candidates)


def test_same_final_promotions_are_serialized_and_leave_one_complete_file(tmp_path: Path) -> None:
    final = tmp_path / "renders" / "final.mp4"
    candidates = []
    for _ in range(2):
        candidate = candidate_path(tmp_path / "run" / "candidates", final)
        _make_video(candidate, duration=1.0)
        candidates.append(candidate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda candidate: _promote(candidate, final), candidates))

    assert final.is_file()
    assert final.stat().st_size > 0
    assert final.read_bytes()  # no zero-byte/partial final after the race
    assert all(result["path"] == str(final.resolve()) for result in results)
    assert not list((tmp_path / "run" / "candidates").glob("*.part.mp4"))


def test_failed_candidate_never_claims_prior_final_after_render_crash(tmp_path: Path) -> None:
    final = tmp_path / "renders" / "final.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    prior = b"prior-known-good"
    final.write_bytes(prior)
    candidate = candidate_path(tmp_path / "run" / "candidates", final)
    candidate.write_bytes(b"partial-render-before-crash")

    with pytest.raises(Exception):
        # The partial bytes are not a valid media container. The prior final
        # must remain the only canonical deliverable.
        promote_candidate(candidate, final)

    assert final.read_bytes() == prior
    assert candidate.exists()
