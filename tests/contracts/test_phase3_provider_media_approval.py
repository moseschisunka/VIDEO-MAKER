"""Local user media must not reach API video providers without approval."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from lib.providers.plans import build_ranked_plan
from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolStatus
from tools.video._shared import upload_image_fal
from tools.video.video_selector import VideoSelector


class _ZeroCostVideoApi(BaseTool):
    name = "zero_cost_video_fixture"
    provider = "fixture-api"
    capability = "video_generation"
    runtime = ToolRuntime.API
    input_schema = {
        "type": "object",
        "properties": {"prompt": {}, "reference_image_path": {}, "image_url": {}},
    }

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_status(self):
        return ToolStatus.AVAILABLE

    def estimate_cost(self, _inputs):
        return 0.0

    def execute(self, inputs):
        self.calls.append(dict(inputs))
        return ToolResult(success=True, data={"output_path": "fixture.mp4"})


def _write_project_image(project_dir: Path) -> Path:
    image_path = project_dir / "assets" / "reference.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), color="red").save(image_path)
    return image_path


def test_direct_api_video_tool_requires_media_approval_even_at_zero_cost(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    image_path = _write_project_image(project_dir)
    monkeypatch.setenv("OPENMONTAGE_PROJECT_DIR", str(project_dir))
    provider = _ZeroCostVideoApi()

    blocked = provider.execute({"prompt": "animate", "reference_image_path": str(image_path)})
    assert not blocked.success
    assert "local media" in (blocked.error or "").lower()
    assert provider.calls == []

    allowed = provider.execute({
        "prompt": "animate",
        "reference_image_path": str(image_path),
        "provider_approved": True,
    })
    assert allowed.success
    assert len(provider.calls) == 1
    approval_schema = provider.get_info(include_status=False)["input_schema"]
    assert "provider_approved" not in approval_schema["properties"]
    assert "provider_approved" not in provider.input_schema["properties"]


def test_approved_api_video_still_rejects_reference_outside_project_media(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_image = tmp_path / "outside.png"
    Image.new("RGB", (4, 4), color="blue").save(outside_image)
    monkeypatch.setenv("OPENMONTAGE_PROJECT_DIR", str(project_dir))
    provider = _ZeroCostVideoApi()

    result = provider.execute({
        "prompt": "animate",
        "reference_image_path": str(outside_image),
        "provider_approved": True,
    })

    assert not result.success
    assert "project assets/ or renders/" in (result.error or "")
    assert provider.calls == []


def test_selector_does_not_upload_local_image_before_transfer_approval(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    image_path = _write_project_image(project_dir)
    monkeypatch.setenv("OPENMONTAGE_PROJECT_DIR", str(project_dir))
    provider = _ZeroCostVideoApi()
    selector = VideoSelector()
    assert "provider_approved" not in selector.input_schema["properties"]
    uploads: list[dict] = []

    def fake_upload(path, *, approved=False, project_dir=None):
        uploads.append({"path": path, "approved": approved})
        return "https://uploads.example/reference.png"

    monkeypatch.setattr("tools.video._shared.upload_image_fal", fake_upload)
    monkeypatch.setattr(selector, "_providers", lambda: [provider])
    monkeypatch.setattr(selector, "_filter_candidates", lambda _inputs, _tools: [provider])
    monkeypatch.setattr(selector, "_prepare_task_context", lambda _inputs: {})
    monkeypatch.setattr(selector, "_select_best_tool", lambda _inputs, _tools, _context: (provider, None))

    request = {
        "prompt": "animate this image",
        "operation": "image_to_video",
        "reference_image_path": str(image_path),
    }
    blocked = selector.execute(request)
    assert not blocked.success
    assert uploads == []
    assert provider.calls == []

    approved = selector.execute({**request, "provider_approved": True})
    assert approved.success
    assert uploads == [{"path": str(image_path), "approved": True}]
    assert provider.calls[0]["image_url"] == "https://uploads.example/reference.png"


def test_dry_run_plan_surfaces_external_media_approval_requirement(tmp_path: Path) -> None:
    provider = _ZeroCostVideoApi()
    plan = build_ranked_plan(
        capability="video_generation",
        operation="image_to_video",
        inputs={"prompt": "animate", "reference_image_path": str(tmp_path / "ref.png")},
        rankings=[{"tool_name": provider.name, "provider": provider.provider}],
        providers=[provider],
    )

    assert plan["external_media_approval_required"] is True
    assert plan["approval_required"] is True
    assert plan["execution"] == "awaiting_approval"


def test_fal_upload_helper_refuses_unapproved_transfer_before_network(
    monkeypatch,
) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("network request must not be made")

    monkeypatch.setattr("requests.post", fail_network)

    try:
        upload_image_fal("outside.png")
    except PermissionError as exc:
        assert "explicit provider approval" in str(exc)
    else:
        raise AssertionError("local image upload was not blocked")
