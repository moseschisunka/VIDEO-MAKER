"""PR-1029 strict policy controls for image and stock-media tools."""

from __future__ import annotations

import pytest

from tools.base_tool import ToolStatus
from tools.graphics.image_selector import ImageSelector
from tools.video.corpus_builder import CorpusBuilder
from tools.video.direct_clip_search import DirectClipSearch


class _ImageProvider:
    name = "stub_image"
    provider = "stub"
    version = "1"
    capability = "image_generation"
    supports = {"generate": True}
    input_schema = {"properties": {"prompt": {}}}
    best_for = ["tests"]

    def get_status(self):
        return ToolStatus.AVAILABLE

    def get_info(self, *args, **kwargs):
        return {
            "name": self.name,
            "provider": self.provider,
            "agent_skills": [],
            "best_for": self.best_for,
            "supports": self.supports,
        }

    def is_operation_available(self, operation: str) -> bool:
        return bool(self.supports.get(operation))


@pytest.mark.parametrize(
    "field,value",
    [
        ("strict_media_validation", "false"),
        ("strict_media_validation", 1),
        ("production_mode", "false"),
        ("production_mode", 1),
    ],
)
def test_image_selector_rejects_malformed_policy_before_provider_call(monkeypatch, field, value):
    provider = _ImageProvider()
    selector = ImageSelector()
    monkeypatch.setattr(selector, "_providers", lambda: [provider])
    monkeypatch.setattr(selector, "_select_best_tool", lambda *args, **kwargs: (provider, None))
    called = False

    def provider_call(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not run with malformed policy")

    monkeypatch.setattr("lib.providers.bridge.execute_with_provider_executor", provider_call)

    result = selector.execute({"prompt": "a precise still", field: value})

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not called


def test_image_selector_rejects_malformed_policy_before_provider_discovery_or_rank(monkeypatch):
    selector = ImageSelector()
    discovery_called = False
    rank_called = False

    def discovery_must_not_run(*args, **kwargs):
        nonlocal discovery_called
        discovery_called = True
        raise AssertionError("provider discovery must not run with malformed policy")

    def rank_must_not_run(*args, **kwargs):
        nonlocal rank_called
        rank_called = True
        raise AssertionError("provider ranking must not run with malformed policy")

    monkeypatch.setattr(selector, "_providers", discovery_must_not_run)
    monkeypatch.setattr("lib.scoring.rank_providers", rank_must_not_run)

    result = selector.execute({"operation": "rank", "prompt": "a still", "production_mode": "false"})

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not discovery_called
    assert not rank_called


@pytest.mark.parametrize(
    "tool,field,value,inputs",
    [
        (DirectClipSearch, "strict_media_validation", "false", {"output_dir": "clips", "queries": [{"query": "x"}]}),
        (DirectClipSearch, "require_provenance", 0, {"output_dir": "clips", "queries": [{"query": "x"}]}),
        (DirectClipSearch, "production_mode", "false", {"output_dir": "clips", "queries": [{"query": "x"}]}),
        (DirectClipSearch, "extract_thumbnails", 1, {"output_dir": "clips", "queries": [{"query": "x"}]}),
        (DirectClipSearch, "skip_existing", "false", {"output_dir": "clips", "queries": [{"query": "x"}]}),
        (CorpusBuilder, "strict_media_validation", "false", {"corpus_dir": "corpus", "queries": [{"query": "x"}]}),
        (CorpusBuilder, "require_provenance", 0, {"corpus_dir": "corpus", "queries": [{"query": "x"}]}),
        (CorpusBuilder, "skip_existing", "false", {"corpus_dir": "corpus", "queries": [{"query": "x"}]}),
    ],
)
def test_stock_media_policy_controls_fail_before_disk_or_source_side_effects(
    tool, field, value, inputs, tmp_path, monkeypatch
):
    target_key = "output_dir" if tool is DirectClipSearch else "corpus_dir"
    target = tmp_path / "media"
    inputs = {**inputs, target_key: str(target), field: value}

    def source_must_not_run(*args, **kwargs):
        raise AssertionError("stock source must not run with malformed policy")

    monkeypatch.setattr("tools.video.stock_sources.available_sources", source_must_not_run)
    monkeypatch.setattr("tools.video.stock_sources.all_sources", source_must_not_run)

    result = tool().execute(inputs)

    assert not result.success
    assert "boolean" in (result.error or "").lower()
    assert not target.exists()


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "not-a-number", None, True, 0, -1])
def test_direct_clip_search_rejects_non_finite_or_non_positive_timeout(tmp_path, value):
    result = DirectClipSearch().execute(
        {
            "output_dir": str(tmp_path / "clips"),
            "queries": [{"query": "x"}],
            "timeout_seconds": value,
        }
    )

    assert not result.success
    assert "timeout_seconds" in (result.error or "")
    assert not (tmp_path / "clips").exists()
