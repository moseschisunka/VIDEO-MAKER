"""Local video providers consume bounded paths; only cloud providers consume URLs."""

from pathlib import Path

import pytest
from PIL import Image

from tools._comfyui.client import ComfyUIError
from tools.video._shared import (
    MAX_REFERENCE_IMAGE_BYTES,
    MAX_REFERENCE_IMAGE_PIXELS,
    load_reference_image,
    validate_reference_image_path,
)
from tools.video.cogvideo_video import CogVideoVideo
from tools.video.comfyui_video import ComfyUIVideo
from tools.video.hunyuan_video import HunyuanVideo
from tools.video.ltx_video_local import LTXVideoLocal
from tools.video.ltx_video_modal import LTXVideoModal
from tools.video.video_selector import VideoSelector
from tools.video.wan_video import WanVideo

LOCAL_VIDEO_TOOLS = (
    ComfyUIVideo,
    WanVideo,
    HunyuanVideo,
    LTXVideoLocal,
    CogVideoVideo,
)


def _project_media_dir(monkeypatch, tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENMONTAGE_PROJECT_DIR", str(project_dir))
    return assets_dir


@pytest.mark.parametrize("tool_class", LOCAL_VIDEO_TOOLS)
def test_local_video_tool_schemas_do_not_accept_reference_urls(tool_class):
    properties = tool_class.input_schema["properties"]

    assert "reference_image_path" in properties
    assert "reference_image_url" not in properties


def test_cloud_video_provider_keeps_reference_url_support():
    assert "reference_image_url" in LTXVideoModal.input_schema["properties"]


def test_local_reference_loader_rejects_url_without_network_request(monkeypatch):
    import requests

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("local video loaders must never download caller-supplied URLs")

    monkeypatch.setattr(requests, "get", fail_if_called)

    result = load_reference_image(
        {"reference_image_url": "http://127.0.0.1/internal"}, 64, 64
    )

    assert result.success is False
    assert "requires reference_image_path" in result.error


def test_comfyui_image_to_video_rejects_url_without_network_request(tmp_path, monkeypatch):
    import requests

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("ComfyUI must not fetch caller-supplied URLs")

    monkeypatch.setattr(requests, "get", fail_if_called)
    tool = ComfyUIVideo()

    with pytest.raises(ComfyUIError, match="requires reference_image_path"):
        tool._build_i2v(
            {"prompt": "animate", "reference_image_url": "http://127.0.0.1/internal"},
            seed=1,
            output_path=tmp_path / "render.mp4",
        )


def test_local_reference_loader_resizes_a_valid_bounded_project_image(tmp_path, monkeypatch):
    assets_dir = _project_media_dir(monkeypatch, tmp_path)
    image_path = assets_dir / "reference.png"
    Image.new("RGB", (8, 4), color="navy").save(image_path)

    image = load_reference_image({"reference_image_path": image_path}, 16, 12)

    assert image.size == (16, 12)


def test_local_reference_loader_rejects_paths_outside_project_media(tmp_path, monkeypatch):
    _project_media_dir(monkeypatch, tmp_path)
    outside_path = tmp_path / "outside.png"
    Image.new("RGB", (8, 4), color="navy").save(outside_path)

    result = load_reference_image({"reference_image_path": outside_path}, 8, 4)

    assert result.success is False
    assert "inside project assets/ or renders/" in result.error


def test_reference_image_byte_limit_is_checked_before_image_decode(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    assets_dir = _project_media_dir(monkeypatch, tmp_path)
    image_path = assets_dir / "oversized.png"
    with image_path.open("wb") as image_file:
        image_file.truncate(MAX_REFERENCE_IMAGE_BYTES + 1)

    def fail_if_decoded(*_args, **_kwargs):
        pytest.fail("oversized reference image must be rejected before decoding")

    monkeypatch.setattr(PILImage, "open", fail_if_decoded)

    with pytest.raises(ValueError, match="MiB limit"):
        validate_reference_image_path(image_path)


def test_reference_image_pixel_limit_is_checked_before_decode(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    assets_dir = _project_media_dir(monkeypatch, tmp_path)
    image_path = assets_dir / "large-dimensions.png"
    image_path.write_bytes(b"header")

    class OversizedImage:
        width = MAX_REFERENCE_IMAGE_PIXELS + 1
        height = 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def verify(self):
            pytest.fail("oversized image dimensions must be rejected first")

    monkeypatch.setattr(PILImage, "open", lambda *_args, **_kwargs: OversizedImage())

    with pytest.raises(ValueError, match="pixel limit"):
        validate_reference_image_path(image_path)


def test_video_selector_routes_by_reference_representation(monkeypatch):
    selector = VideoSelector()
    monkeypatch.setattr(selector, "_operation_ready", lambda *_args: True)
    local_tool = WanVideo()
    cloud_tool = LTXVideoModal()

    url_candidates = selector._filter_candidates(
        {
            "operation": "image_to_video",
            "reference_image_url": "https://example.com/reference.png",
        },
        [local_tool, cloud_tool],
    )
    path_candidates = selector._filter_candidates(
        {
            "operation": "image_to_video",
            "reference_image_path": "projects/demo/assets/reference.png",
        },
        [local_tool, cloud_tool],
    )

    assert [tool.name for tool in url_candidates] == [cloud_tool.name]
    assert local_tool in path_candidates


def test_video_selector_does_not_fall_back_to_local_tool_for_url_only():
    selector = VideoSelector()
    local_tool = WanVideo()

    candidates = selector._filter_candidates(
        {
            "operation": "image_to_video",
            "reference_image_url": "https://example.com/reference.png",
        },
        [local_tool],
    )

    assert candidates == []
