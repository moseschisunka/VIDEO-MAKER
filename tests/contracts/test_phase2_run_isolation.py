"""PR-203 contracts for run-scoped renderer workspaces."""

from __future__ import annotations

import json
from pathlib import Path

from lib.checkpoint import init_project
from lib.paths import RunPathError, run_paths
from tools.video.video_compose import VideoCompose


RUN_ID = "12345678-1234-4234-8234-123456789abc"


def test_run_paths_are_uuid_scoped_and_contained(tmp_path: Path) -> None:
    project = init_project(
        "isolated-project",
        title="Isolated",
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    paths = run_paths(project, RUN_ID)
    assert paths.root == project / "runs" / RUN_ID
    assert paths.work == paths.root / "work"
    assert paths.inputs == paths.root / "inputs"
    assert paths.props == paths.root / "props"
    assert paths.logs == paths.root / "logs"
    assert paths.candidates == paths.root / "candidates"
    assert paths.reports == paths.root / "reports"
    assert all(path.is_dir() for path in (
        paths.work, paths.inputs, paths.props, paths.logs,
        paths.candidates, paths.reports,
    ))

    try:
        run_paths(project, "../../outside")
    except RunPathError:
        pass
    else:  # pragma: no cover - assertion keeps traversal from regressing
        raise AssertionError("path traversal run_id was accepted")


def test_remotion_uses_run_scoped_props_and_public_assets(tmp_path: Path, monkeypatch) -> None:
    project = init_project(
        "remotion-isolated-project",
        title="Remotion isolated",
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    source = project / "assets" / "video" / "source.mp4"
    source.write_bytes(b"fixture source")

    captured: dict[str, object] = {}

    def fake_promote(_self, *, candidate, final_path, **_kwargs):
        Path(final_path).write_bytes(Path(candidate).read_bytes())
        Path(candidate).unlink()
        return {"path": str(final_path), "candidate_path": str(candidate)}

    def fake_run_command(_self, cmd, *, timeout=None, cwd=None):
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        output = next(Path(item) for item in cmd if ".part.mp4" in str(item))
        output.write_bytes(b"rendered")
        return None

    monkeypatch.setattr(VideoCompose, "run_command", fake_run_command)
    monkeypatch.setattr(VideoCompose, "_promote_run_output", fake_promote)
    result = VideoCompose()._remotion_render({
        "project_dir": str(project),
        "run_id": RUN_ID,
        "output_path": "renders/isolated.mp4",
        "profile": "youtube_landscape",
        "composition_data": {
            "renderer_family": "screen-demo",
            "cuts": [{
                "source": "assets/video/source.mp4",
                "in_seconds": 0,
                "out_seconds": 1,
            }],
        },
    })
    assert result.success, result.error
    run_root = project / "runs" / RUN_ID
    assert Path(result.data["run_dir"]) == run_root
    assert Path(result.data["props_path"]).is_file()
    assert Path(result.data["props_path"]).parent == run_root / "props"
    public_dir = Path(result.data["public_dir"])
    assert public_dir.is_dir()
    assert public_dir.is_relative_to(run_root / "inputs")
    staged = list((public_dir / "staged_assets").glob("*_source.mp4"))
    assert len(staged) == 1

    command = captured["cmd"]
    assert any(str(run_root / "props") in str(item) for item in command)
    assert any(str(public_dir) in str(item) for item in command)
    # The composer-level shared scratch directory must not be selected for a
    # durable project run.
    assert not any("remotion-composer\\public\\staged_assets" in str(item).lower() for item in command)


def test_ffmpeg_scratch_is_run_scoped_and_cleaned(tmp_path: Path, monkeypatch) -> None:
    project = init_project(
        "ffmpeg-isolated-project",
        title="FFmpeg isolated",
        pipeline_type="screen-demo",
        run_id=RUN_ID,
        pipeline_dir=tmp_path,
    )
    source = project / "assets" / "video" / "source.mp4"
    source.write_bytes(b"fixture source")
    calls: list[list[str]] = []

    def fake_promote(_self, *, candidate, final_path, **_kwargs):
        Path(final_path).write_bytes(Path(candidate).read_bytes())
        Path(candidate).unlink()
        return {"path": str(final_path), "candidate_path": str(candidate)}

    def fake_run_command(_self, cmd, *, timeout=None, cwd=None):
        calls.append([str(item) for item in cmd])
        output = Path(cmd[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"segment")
        return None

    monkeypatch.setattr(VideoCompose, "run_command", fake_run_command)
    monkeypatch.setattr(VideoCompose, "_promote_run_output", fake_promote)
    monkeypatch.setattr(VideoCompose, "_has_audio_stream", staticmethod(lambda _path: False))
    result = VideoCompose()._compose({
        "project_dir": str(project),
        "run_id": RUN_ID,
        "output_path": "renders/isolated.mp4",
        "edit_decisions": {
            "cuts": [{
                "source": "assets/video/source.mp4",
                "in_seconds": 0,
                "out_seconds": 1,
            }],
        },
    })
    assert result.success, result.error
    run_root = project / "runs" / RUN_ID
    assert Path(result.data["run_dir"]) == run_root
    assert Path(result.data["staging_dir"]).is_relative_to(run_root / "work")
    assert not Path(result.data["staging_dir"]).exists()
    assert calls
    assert any(str(run_root / "work") in command for call in calls for command in call)
