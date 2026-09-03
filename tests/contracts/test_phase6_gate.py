"""Phase 6 mixed-media ingestion, provenance, approval, and resume gate."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import lib.media_ingestion as media_ingestion
from lib.asset_cache import AssetCache
from lib.contact_sheet import ContactSheetError, approve_contact_sheet, build_contact_sheet_manifest, write_contact_sheet
from lib.diagram_contracts import DiagramContractError, validate_diagram_spec
from lib.media_contracts import (
    AssetRequest,
    AssetResult,
    MediaContractError,
    build_asset_request,
    validate_asset_request,
    validate_asset_result,
    validate_mixed_media_coverage,
)
from lib.media_generation import (
    GenerationValidationError,
    asset_result_from_output,
    build_generation_plan,
    require_sample_approval,
    validate_generation_output,
)
from lib.media_ingestion import MediaValidationError, download_stream_atomic, ingest_user_media, rank_asset_candidates, validate_media_file
from tools.video.stock_sources.base import Candidate, stock_provenance


ROOT = Path(__file__).resolve().parents[2]


def _png(path: Path, color: str = "#1f6feb") -> Path:
    from PIL import Image

    image = Image.new("RGB", (640, 360), color)
    image.save(path, format="PNG")
    return path


def _video(path: Path, *, moving: bool) -> Path:
    source = "testsrc=size=320x180:rate=12:duration=1" if moving else "color=c=blue:size=320x180:rate=12:duration=1"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_phase6_asset_contracts_and_mixed_media_coverage(tmp_path: Path):
    user_path = _video(tmp_path / "interview.mp4", moving=True)
    stock_path = _png(tmp_path / "stock.png")
    ai_path = _png(tmp_path / "generated.png", "#b42318")
    diagram_path = _png(tmp_path / "diagram.png", "#0f766e")
    user_facts = validate_media_file(user_path, "video")
    stock_facts = validate_media_file(stock_path, "image")
    ai_facts = validate_media_file(ai_path, "image")
    diagram_facts = validate_media_file(diagram_path, "diagram")
    assets = [
        {"id": "user-1", "type": "video", "path": str(user_path), "scene_id": "s1", "strategy": "user_media", "source_tool": "upload", "sha256": user_facts["sha256"], "validation": {**user_facts, "consent": True}},
        {"id": "stock-1", "type": "image", "path": str(stock_path), "scene_id": "s2", "strategy": "stock", "source_tool": "pexels", "sha256": stock_facts["sha256"], "source_url": "https://pexels.test/photo/1", "creator": "A. Photographer", "license": "Pexels License"},
        {"id": "ai-1", "type": "image", "path": str(ai_path), "scene_id": "s3", "strategy": "ai", "source_tool": "image_selector", "provider": "test-image", "model": "model-v1", "prompt": "warm red product illustration", "sha256": ai_facts["sha256"]},
        {"id": "diagram-1", "type": "diagram", "path": str(diagram_path), "scene_id": "s4", "strategy": "diagram", "source_tool": "diagram_gen", "sha256": diagram_facts["sha256"], "validation": {"semantic_validation": {"valid": True, "label_count": 3}}},
    ]
    contact = build_contact_sheet_manifest([
        {"asset_id": "user-1", "scene_id": "s1", "provider": "upload", "strategy": "user_media", "path": str(user_path), "cost_usd": 0},
        {"asset_id": "stock-1", "scene_id": "s2", "provider": "pexels", "strategy": "stock", "path": str(stock_path), "source_url": "https://pexels.test/photo/1", "creator": "A. Photographer", "license": "Pexels License", "cost_usd": 0},
        {"asset_id": "ai-1", "scene_id": "s3", "provider": "test-image", "strategy": "ai", "path": str(ai_path), "model": "model-v1", "prompt": "warm red product illustration", "cost_usd": 0.04},
        {"asset_id": "diagram-1", "scene_id": "s4", "provider": "diagram_gen", "strategy": "diagram", "path": str(diagram_path), "cost_usd": 0},
    ], batch_id="batch-1")
    contact["approval_status"] = "approved"
    contact["approved_candidate_ids"] = ["user-1", "stock-1", "ai-1", "diagram-1"]
    contact["approval"] = approve_contact_sheet(contact, {"approved": True, "approval_id": "approval-1", "manifest_hash": contact["manifest_hash"]})
    edits = {"cuts": [{"id": "c1", "source": "user-1", "in_seconds": 0, "out_seconds": 1}, {"id": "c2", "source": "stock-1", "in_seconds": 1, "out_seconds": 2}, {"id": "c3", "source": "ai-1", "in_seconds": 2, "out_seconds": 3}, {"id": "c4", "source": "diagram-1", "in_seconds": 3, "out_seconds": 4}]}
    coverage = validate_mixed_media_coverage({"assets": assets}, edits, contact_sheet=contact)
    assert coverage["valid"] is True, coverage["errors"]


def test_phase6_user_media_and_download_fail_closed(tmp_path: Path):
    source = _png(tmp_path / "source.png")
    facts = ingest_user_media(source, tmp_path / "project" / "assets" / "source.png", media_type="image", consent=True, allowed_root=tmp_path / "project")
    assert facts["consent"] is True and facts["sha256"]
    with pytest.raises(MediaValidationError, match="consent"):
        ingest_user_media(source, tmp_path / "project" / "assets" / "bad.png", media_type="image", consent=False, allowed_root=tmp_path / "project")
    with pytest.raises(MediaValidationError, match="escapes"):
        ingest_user_media(source, tmp_path / "outside.png", media_type="image", consent=True, allowed_root=tmp_path / "project")

    payload = source.read_bytes()

    class Response:
        status_code = 200
        headers = {"content-type": "image/png", "content-length": str(len(payload))}

        def iter_content(self, chunk_size=0):
            yield payload[:20]
            yield payload[20:]

        def close(self):
            pass

    downloaded = download_stream_atomic("https://example.test/image.png", tmp_path / "download.png", media_type="image", request_get=lambda *args, **kwargs: Response())
    assert downloaded["sha256"] == facts["sha256"]

    class BadResponse(Response):
        headers = {"content-type": "text/html", "content-length": "5"}

        def iter_content(self, chunk_size=0):
            yield b"<html>"

    with pytest.raises(MediaValidationError, match="non-media MIME"):
        download_stream_atomic("https://example.test/bad", tmp_path / "bad.png", media_type="image", request_get=lambda *args, **kwargs: BadResponse())
    assert not (tmp_path / "bad.png.part").exists()


def test_phase6_media_ingestion_rejects_zero_duration_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decodable container with no positive duration is not usable media."""

    candidate = tmp_path / "zero-duration.mp4"
    # Enough bytes to pass the size floor and an ISO-BMFF magic signature; the
    # ffprobe response below is the authoritative decode result for this
    # contract fixture.
    candidate.write_bytes(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 256))

    class ProbeResult:
        stdout = (
            '{"streams":[{"codec_type":"video","width":320,"height":180}],'
            '"format":{"duration":"0"}}'
        )

    monkeypatch.setattr(media_ingestion.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_ingestion.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    with pytest.raises(MediaValidationError, match="positive duration"):
        media_ingestion.validate_media_file(candidate, "video")


@pytest.mark.parametrize("duration", ["NaN", "inf", "-inf", "-0.5"])
def test_phase6_media_ingestion_rejects_nonfinite_or_negative_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duration: str
) -> None:
    """Non-finite and negative probe durations cannot enter a timeline."""

    candidate = tmp_path / f"invalid-duration-{duration.replace('-', 'neg').replace('.', '_')}.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 256))

    class ProbeResult:
        stdout = (
            '{"streams":[{"codec_type":"video","width":320,"height":180}],'
            f'"format":{{"duration":"{duration}"}}}}'
        )

    monkeypatch.setattr(media_ingestion.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_ingestion.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    with pytest.raises(MediaValidationError, match="positive duration"):
        media_ingestion.validate_media_file(candidate, "video")


def test_phase6_media_ingestion_uses_positive_stream_duration_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid stream duration is retained when the container omits one."""

    candidate = tmp_path / "stream-duration.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 256))

    class ProbeResult:
        stdout = (
            '{"streams":[{"codec_type":"video","width":320,"height":180,'
            '"duration":"1.25"}],"format":{"duration":"N/A"}}'
        )

    monkeypatch.setattr(media_ingestion.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_ingestion.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    facts = media_ingestion.validate_media_file(candidate, "video")

    assert facts["decoded"] is True
    assert facts["duration_seconds"] == 1.25
    assert facts["duration_source"] == "stream"


def test_phase6_media_ingestion_prefers_container_duration_over_longer_audio_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A longer companion audio stream must not extend the video timeline."""

    candidate = tmp_path / "container-duration.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 256))

    class ProbeResult:
        stdout = (
            '{"streams":['
            '{"codec_type":"video","duration":"1.25"},'
            '{"codec_type":"audio","duration":"9.5"}],'
            '"format":{"duration":"1.25"}}'
        )

    monkeypatch.setattr(media_ingestion.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_ingestion.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    facts = media_ingestion.validate_media_file(candidate, "video")

    assert facts["decoded"] is True
    assert facts["duration_seconds"] == 1.25
    assert facts["duration_source"] == "format"


def test_phase6_media_ingestion_stream_fallback_ignores_unrelated_longer_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When format duration is unavailable, use the declared stream timeline."""

    candidate = tmp_path / "typed-stream-duration.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 256))

    class ProbeResult:
        stdout = (
            '{"streams":['
            '{"codec_type":"video","duration":"1.25"},'
            '{"codec_type":"audio","duration":"9.5"}],'
            '"format":{"duration":"N/A"}}'
        )

    monkeypatch.setattr(media_ingestion.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_ingestion.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    facts = media_ingestion.validate_media_file(candidate, "video")

    assert facts["decoded"] is True
    assert facts["duration_seconds"] == 1.25
    assert facts["duration_source"] == "stream"


def test_phase6_media_ingestion_rejects_missing_declared_stream_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A video-only probe cannot satisfy an audio asset declaration."""

    candidate = tmp_path / "video-as-audio.mp3"
    # Match the declared audio family so the ffprobe stream-type contract is
    # reached instead of being short-circuited by the MIME-family guard.
    candidate.write_bytes(b"ID3" + (b"\x00" * 256))

    class ProbeResult:
        stdout = (
            '{"streams":[{"codec_type":"video","width":320,"height":180,'
            '"duration":"1.25"}],"format":{"duration":"1.25"}}'
        )

    monkeypatch.setattr(media_ingestion.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_ingestion.subprocess, "run", lambda *args, **kwargs: ProbeResult())

    with pytest.raises(MediaValidationError, match="no audio stream"):
        media_ingestion.validate_media_file(candidate, "audio")


def test_phase6_generation_sample_plan_cache_and_motion(tmp_path: Path):
    request = AssetRequest("r1", "scene-1", "a moving product reveal", "video", "ai", {"min_duration_seconds": 0.5}, sample_required=True)
    plan = build_generation_plan(request, provider="test-video", model="v1", inputs={"prompt": request.intent, "operation": "text_to_video"})
    with pytest.raises(GenerationValidationError, match="sample approval"):
        require_sample_approval(request, None, plan_id=plan["plan_id"])
    approval = require_sample_approval(request, {"approved": True, "plan_id": plan["plan_id"], "approval_id": "a1"}, plan_id=plan["plan_id"])
    assert approval["approved"] is True
    moving = _video(tmp_path / "moving.mp4", moving=True)
    static = _video(tmp_path / "static.mp4", moving=False)
    valid = validate_generation_output({"artifacts": [str(moving)]}, media_type="video", motion_required=True)
    assert valid["valid"] is True and valid["outputs"][0]["motion_score"] > 0
    with pytest.raises(GenerationValidationError, match="static"):
        validate_generation_output({"artifacts": [str(static)]}, media_type="video", motion_required=True)
    result = asset_result_from_output(request, moving, provider="test-video", model="v1", prompt=request.intent)
    assert validate_asset_result(result, request=request)["valid"] is True
    cache = AssetCache(tmp_path / "asset-cache")
    entry = cache.put(request, moving, asset_id=result.asset_id, validation=valid["outputs"][0], metadata={"plan_id": plan["plan_id"]})
    hit = cache.get(request, destination=tmp_path / "resumed.mp4")
    assert entry.sha256 == hit["sha256"] and Path(hit["path"]).is_file()
    Path(entry.path if Path(entry.path).is_absolute() else cache.cache_dir / entry.path).write_bytes(b"corrupt")
    assert cache.get(request) is None


@pytest.mark.parametrize("field,value", [("sample_required", "false"), ("sample_required", 0), ("provenance_required", "false"), ("provenance_required", 0)])
def test_phase6_asset_request_rejects_non_boolean_policy_flags(field: str, value: object):
    raw = {
        "request_id": "r1",
        "scene_id": "scene-1",
        "intent": "a moving product reveal",
        "media_type": "video",
        "strategy": "ai",
        field: value,
    }
    with pytest.raises(MediaContractError, match=f"{field} must be boolean"):
        build_asset_request(raw)


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_phase6_contact_sheet_rejects_malformed_approval_requirement(value: object):
    candidate = {
        "asset_id": "a1",
        "scene_id": "s1",
        "provider": "pexels",
        "strategy": "stock",
        "source_url": "https://example.test/a1",
        "creator": "Creator",
        "license": "Pexels",
        "cost_usd": 0,
    }
    with pytest.raises(ContactSheetError, match="required_approval must be boolean"):
        build_contact_sheet_manifest([candidate], batch_id="batch-policy", required_approval=value)


def test_phase6_stock_ranking_provenance_and_duplicate_rejection():
    candidate = Candidate(
        source="pexels", source_id="1", source_url="https://pexels.test/1", download_url="https://cdn.test/1.mp4",
        kind="video", creator="Creator", license="Pexels License", source_tags="city rain night", width=1920, height=1080,
    )
    provenance = stock_provenance(candidate, retrieval_time="2026-09-02T00:00:00+00:00")
    assert provenance["license_url"].startswith("https://")
    ranked = rank_asset_candidates([
        {"asset_id": "a", "source": "pexels", "source_url": "https://pexels.test/a", "source_tags": "city rain", "width": 1920, "height": 1080, "quality_score": 0.9},
        {"asset_id": "b", "source": "pixabay", "source_url": "https://pixabay.test/b", "source_tags": "city rain", "width": 1920, "height": 1080, "quality_score": 0.8},
        {"asset_id": "c", "source": "pixabay", "source_url": "https://pixabay.test/b", "source_tags": "city rain", "width": 1920, "height": 1080, "quality_score": 0.8},
        {"asset_id": "d", "source": "pixabay", "source_url": "https://pixabay.test/d", "source_tags": "city rain", "width": 1920, "height": 1080, "quality_score": 0.7},
    ], intent="city rain", orientation="landscape", max_per_source=1)
    assert ranked[0]["rejected"] is False
    assert any(item["rejected"] and "duplicate" in " ".join(item["reasons"]) for item in ranked)
    assert any(item["rejected"] and "diversity" in " ".join(item["reasons"]) for item in ranked)
    with pytest.raises(ValueError, match="provenance"):
        stock_provenance(Candidate(source="x", source_id="y", source_url="", download_url="x", kind="image"))


def test_phase6_diagram_and_contact_sheet_render(tmp_path: Path):
    semantic = validate_diagram_spec({"diagram_type": "boxes", "boxes": [{"label": "Input"}, {"label": "Output"}], "connections": [{"from": 0, "to": 1, "label": "result"}]})
    assert semantic["label_count"] == 2
    with pytest.raises(DiagramContractError, match="label"):
        validate_diagram_spec({"diagram_type": "boxes", "boxes": [{"label": ""}]})
    manifest = build_contact_sheet_manifest([{"asset_id": "a1", "scene_id": "s1", "provider": "pexels", "strategy": "stock", "source_url": "https://example.test/a1", "creator": "Creator", "license": "Pexels", "cost_usd": 0}], batch_id="batch-render")
    rendered = write_contact_sheet(manifest, tmp_path / "contact.png")
    assert Path(rendered["path"]).is_file()


def test_phase6_selector_sample_gate_runs_before_provider(monkeypatch):
    from tools.base_tool import ToolResult, ToolStatus
    from tools.graphics.image_selector import ImageSelector

    class DummyProvider:
        name = "dummy_image"
        provider = "dummy"
        version = "v1"
        input_schema = {"properties": {"prompt": {}, "output_path": {}}}
        best_for = []
        supports = {"image_edit": False}

        def get_status(self):
            return ToolStatus.AVAILABLE

        def estimate_cost(self, inputs):
            return 0.0

        def get_info(self, *args, **kwargs):
            return {"agent_skills": [], "best_for": []}

        def execute(self, inputs):  # pragma: no cover - must not be called
            raise AssertionError("provider called before sample approval")

    provider = DummyProvider()
    selector = ImageSelector()
    monkeypatch.setattr(selector, "_providers", lambda: [provider])
    monkeypatch.setattr(selector, "_select_best_tool", lambda *args, **kwargs: (provider, None))
    result = selector.execute({"prompt": "approved image", "batch": True})
    assert result.success is False
    assert "sample" in (result.error or "").lower()


@pytest.mark.parametrize("field,value", [("sample_required", 0), ("batch", "false")])
def test_phase6_image_selector_rejects_malformed_sample_controls(monkeypatch, field: str, value: object):
    from tools.base_tool import ToolResult, ToolStatus
    from tools.graphics.image_selector import ImageSelector

    class DummyProvider:
        name = "dummy_image"
        provider = "dummy"
        version = "v1"
        input_schema = {"properties": {"prompt": {}, "output_path": {}}}
        best_for = []
        supports = {"image_edit": False}

        def get_status(self):
            return ToolStatus.AVAILABLE

        def estimate_cost(self, inputs):
            return 0.0

        def get_info(self, *args, **kwargs):
            return {"agent_skills": [], "best_for": []}

        def execute(self, inputs):  # pragma: no cover - must not be called
            raise AssertionError("provider called with malformed sample control")

    provider = DummyProvider()
    selector = ImageSelector()
    monkeypatch.setattr(selector, "_providers", lambda: [provider])
    monkeypatch.setattr(selector, "_select_best_tool", lambda *args, **kwargs: (provider, None))
    result = selector.execute({"prompt": "sample control", field: value})
    assert result.success is False
    assert "boolean" in (result.error or "").lower()


def test_phase6_gate_keeps_global_production_lock():
    tracker = (ROOT / "docs" / "production-readiness" / "PROGRESS_TRACKER.md").read_text(encoding="utf-8")
    # The tracker advances as later phases pass; the global lock must remain
    # true regardless of the current phase.
    assert "Current phase | Phase 10" in tracker
    assert "Production decision | Not eligible" in tracker
    assert "PR-11G" in tracker
