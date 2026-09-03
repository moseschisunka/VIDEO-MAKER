"""Contract tests for Pexels option discovery and selected downloads."""

from __future__ import annotations

import sys
import types

from tools.graphics.pexels_image import PexelsImage
from tools.video.pexels_video import PexelsVideo


class _Response:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_pexels_image_returns_options_without_downloading(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-pexels-key")
    calls = []
    photos = [
        {
            "id": 10,
            "photographer": "A",
            "photographer_url": "https://www.pexels.com/@a",
            "alt": "A classroom",
            "width": 1600,
            "height": 900,
            "url": "https://www.pexels.com/photo/10/",
            "src": {"medium": "https://img/10-medium", "large2x": "https://img/10"},
        },
        {
            "id": 11,
            "photographer": "B",
            "alt": "A teacher",
            "width": 1600,
            "height": 900,
            "url": "https://www.pexels.com/photo/11/",
            "src": {"medium": "https://img/11-medium", "large2x": "https://img/11"},
        },
    ]
    fake = types.ModuleType("requests")

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response({"total_results": 20, "photos": photos})

    fake.get = get
    monkeypatch.setitem(sys.modules, "requests", fake)

    result = PexelsImage().execute({"query": "classroom", "per_page": 2, "download": False})

    assert result.success, result.error
    assert len(result.data["options"]) == 2
    assert result.data["selected_index"] == 0
    assert result.data["output"] is None
    assert len(calls) == 1
    assert calls[0][1]["headers"] == {"Authorization": "test-pexels-key"}


def test_pexels_video_downloads_selected_option(monkeypatch, tmp_path):
    monkeypatch.setenv("PEXELS_API_KEY", "test-pexels-key")
    calls = []
    videos = [
        {
            "id": 20,
            "duration": 8,
            "user": {"name": "A"},
            "image": "https://img/20.jpg",
            "url": "https://www.pexels.com/video/20/",
            "video_files": [{"quality": "hd", "width": 1280, "height": 720, "fps": 30, "link": "https://vid/20.mp4"}],
        },
        {
            "id": 21,
            "duration": 12,
            "user": {"name": "B"},
            "image": "https://img/21.jpg",
            "url": "https://www.pexels.com/video/21/",
            "video_files": [{"quality": "hd", "width": 1920, "height": 1080, "fps": 30, "link": "https://vid/21.mp4"}],
        },
    ]
    fake = types.ModuleType("requests")

    def get(url, **kwargs):
        calls.append(url)
        if url == "https://api.pexels.com/videos/search":
            return _Response({"total_results": 2, "videos": videos})
        return _Response(content=b"selected-video")

    fake.get = get
    monkeypatch.setitem(sys.modules, "requests", fake)
    output = tmp_path / "selected.mp4"

    result = PexelsVideo().execute({
        "query": "teacher demonstration",
        "select_index": 1,
        "output_path": str(output),
    })

    assert result.success, result.error
    assert result.data["selected_index"] == 1
    assert result.data["video_id"] == 21
    assert result.data["options"][0]["video_id"] == 20
    assert output.read_bytes() == b"selected-video"
    assert calls == ["https://api.pexels.com/videos/search", "https://vid/21.mp4"]
