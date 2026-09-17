"""Contracts for the project-scoped MCP bridge used by local directors."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import agent_mcp
from lib.agent_mcp import AgentBridgeError, LocalDirectorBridge
from lib.pipeline_loader import load_pipeline_readonly
from lib.work_order import build_work_order, claim_work_order, read_work_order, write_work_order
from tools.base_tool import BaseTool, ToolResult


PROJECT_ID = "local-director-test"
RUN_ID = "12345678-1234-4234-8234-123456789abc"
REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeRegistry:
    def __init__(self, tool: BaseTool) -> None:
        self.tool = tool

    def get(self, name: str):
        return self.tool if name == self.tool.name else None


class _ProbeTool(BaseTool):
    name = "probe"
    provider = "selector"
    capability = "test"
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "source_path": {"type": "string"},
            "project_dir": {"type": "string"},
            "approved": {"type": "boolean"},
            "asset_request": {"type": "object", "additionalProperties": True},
            "sample_approval": {"type": "object"},
        },
        "required": ["text", "source_path", "project_dir"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.last_inputs = None

    def execute(self, inputs):
        self.last_inputs = dict(inputs)
        return ToolResult(success=True, data={"accepted": True})


def _project(tmp_path: Path) -> Path:
    project = tmp_path / PROJECT_ID
    (project / "artifacts").mkdir(parents=True)
    (project / "assets" / "video").mkdir(parents=True)
    (project / "renders").mkdir()
    manifest = load_pipeline_readonly("screen-demo", defs_dir=REPO_ROOT / "pipeline_defs")
    order = build_work_order(
        project_id=PROJECT_ID,
        title="Local director test",
        topic_prompt="Test the local OpenMontage tool bridge.",
        target_duration_seconds=30,
        pipeline_type="screen-demo",
        manifest=manifest,
        manifest_path=REPO_ROOT / "pipeline_defs" / "screen-demo.yaml",
        selections={
            "playbook": "premium-minimalist",
            "voice": "en-US-ChristopherNeural",
            "voice_provider": "edge_tts",
            "render_runtime": "ffmpeg",
            "output_profile": "youtube_landscape",
            "aspect_ratio": "16:9",
            "source_mode": "real_capture",
        },
        run_id=RUN_ID,
    )
    (project / "project.json").write_text(json.dumps({
        "version": "1.0",
        "project_id": PROJECT_ID,
        "title": "Local director test",
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "style_playbook": "premium-minimalist",
    }), encoding="utf-8")
    (project / "artifacts" / "project_config.json").write_text(json.dumps({
        "project_id": PROJECT_ID,
        "pipeline_type": "screen-demo",
        "run_id": RUN_ID,
        "title": "Local director test",
    }), encoding="utf-8")
    (project / "events.jsonl").write_text("{}\n", encoding="utf-8")
    write_work_order(project, order)
    claim_work_order(project, "agent-a")
    return project


def _fake_context(project: Path):
    order = read_work_order(project)
    return SimpleNamespace(
        order=order,
        stage="idea",
        stage_definition={
            "tools_available": ["probe"],
            "required_tools": ["probe"],
            "produces": ["brief", "decision_log"],
            "required_artifacts_in": [],
        },
        required_artifacts_in=(),
        produced_artifacts=("brief", "decision_log"),
        as_dict=lambda: {
            "project_id": PROJECT_ID,
            "pipeline_type": "screen-demo",
            "run_id": RUN_ID,
            "next_stage": "idea",
        },
    )


def _bridge(project: Path, tool: BaseTool, monkeypatch) -> LocalDirectorBridge:
    monkeypatch.setattr(agent_mcp, "load_manifest_stage_context", lambda _root: _fake_context(project))
    return LocalDirectorBridge(
        project_dir=project,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        agent_id="agent-a",
        capability_validator=lambda: None,
        tool_registry=_FakeRegistry(tool),
    )


def test_bridge_fails_closed_without_a_run_capability(tmp_path) -> None:
    with pytest.raises(AgentBridgeError, match="short-lived Backlot run capability"):
        LocalDirectorBridge(
            project_dir=tmp_path,
            project_id=PROJECT_ID,
            run_id=RUN_ID,
            agent_id="agent-a",
        )


def test_environment_bridge_requires_capability_and_loopback_backlot() -> None:
    context = {
        "OPENMONTAGE_PROJECT_DIR": str(REPO_ROOT),
        "OPENMONTAGE_PROJECT_ID": PROJECT_ID,
        "OPENMONTAGE_RUN_ID": RUN_ID,
        "OPENMONTAGE_AGENT_ID": "agent-a",
        "OPENMONTAGE_BACKLOT_URL": "http://127.0.0.1:8000",
    }
    with pytest.raises(AgentBridgeError, match="OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN"):
        LocalDirectorBridge.from_environment(context)

    with pytest.raises(AgentBridgeError, match="on this device"):
        LocalDirectorBridge.from_environment({
            **context,
            "OPENMONTAGE_BACKLOT_URL": "https://untrusted.example",
            "OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN": "not-a-real-secret",
        })


def test_capability_client_uses_a_direct_loopback_request_without_proxy_env(
    tmp_path,
    monkeypatch,
) -> None:
    observed = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ok": True,
                "project_id": PROJECT_ID,
                "run_id": RUN_ID,
                "agent_id": "agent-a",
            }

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            observed.update(url=url, kwargs=kwargs, trust_env=self.trust_env)
            return Response()

    monkeypatch.setattr(agent_mcp.requests, "Session", Session)
    client = agent_mcp._BacklotCapabilityClient(
        "http://127.0.0.1:8000",
        "scoped-test-token",
    )

    client.validate(
        project_dir=tmp_path,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        agent_id="agent-a",
    )

    assert observed["url"] == "http://127.0.0.1:8000/api/local-director/capability/validate"
    assert observed["trust_env"] is False
    assert observed["kwargs"]["allow_redirects"] is False
    assert observed["kwargs"]["headers"]["Authorization"] == "Bearer scoped-test-token"
    assert observed["kwargs"]["json"] == {
        "project_dir": str(tmp_path.resolve()),
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "agent_id": "agent-a",
    }


def test_context_hides_bound_identity_and_only_advertises_stage_tools(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    bridge = _bridge(project, _ProbeTool(), monkeypatch)

    context = bridge.get_context()

    assert [item["name"] for item in context["stage_tools"]] == ["probe"]
    schema = context["stage_tools"][0]["input_schema"]
    assert "project_dir" not in schema["properties"]
    assert "approved" not in schema["properties"]
    assert "project_dir" not in schema["required"]
    assert "approved" not in schema["required"]
    assert "provider approval" in " ".join(context["rules"]).lower()


def test_tool_execution_binds_run_and_project_scopes_paths(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    tool = _ProbeTool()
    bridge = _bridge(project, tool, monkeypatch)

    result = bridge._execute_tool("probe", {
        "text": "inspect the source",
        "source_path": "assets/video/source.mp4",
    })

    assert result["success"] is True
    assert tool.last_inputs["project_dir"] == str(project.resolve())
    assert tool.last_inputs["project_id"] == PROJECT_ID
    assert tool.last_inputs["run_id"] == RUN_ID
    assert tool.last_inputs["agent_id"] == "agent-a"
    assert tool.last_inputs["stage"] == "idea"
    assert tool.last_inputs["provider_approved"] is False
    assert tool.last_inputs["source_path"] == str((project / "assets/video/source.mp4").resolve())


@pytest.mark.parametrize(
    "inputs",
    [
        {"text": "x", "source_path": "assets/video/source.mp4", "approved": True},
        {"text": "x", "source_path": "assets/video/source.mp4", "project_dir": "C:/outside"},
    ],
)
def test_tool_execution_rejects_model_authored_controls(tmp_path, monkeypatch, inputs) -> None:
    project = _project(tmp_path)
    bridge = _bridge(project, _ProbeTool(), monkeypatch)

    with pytest.raises(AgentBridgeError, match="cannot set server-bound identity or approval"):
        bridge._execute_tool("probe", inputs)


@pytest.mark.parametrize(
    "inputs",
    [
        {
            "text": "x",
            "source_path": "assets/video/source.mp4",
            "asset_request": {"provider_approved": True},
        },
        {
            "text": "x",
            "source_path": "assets/video/source.mp4",
            "asset_request": {"sample_approval": {"status": "approved"}},
        },
    ],
)
def test_tool_execution_rejects_nested_approval_controls(tmp_path, monkeypatch, inputs) -> None:
    project = _project(tmp_path)
    bridge = _bridge(project, _ProbeTool(), monkeypatch)

    with pytest.raises(AgentBridgeError, match="cannot set server-bound identity or approval"):
        bridge._execute_tool("probe", inputs)


def test_public_input_schema_removes_nested_approval_controls() -> None:
    schema = agent_mcp.public_input_schema({
        "type": "object",
        "properties": {
            "asset_request": {
                "type": "object",
                "properties": {
                    "sample_approval": {"type": "object"},
                    "providerApproved": {"type": "boolean"},
                    "sample_required": {"type": "boolean"},
                },
                "required": ["sample_approval", "sample_required"],
            },
        },
    })

    nested = schema["properties"]["asset_request"]
    assert set(nested["properties"]) == {"sample_required"}
    assert nested["required"] == ["sample_required"]


def test_tool_execution_rejects_paths_outside_project_and_control_files(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    bridge = _bridge(project, _ProbeTool(), monkeypatch)

    with pytest.raises(AgentBridgeError, match="inside the active project"):
        bridge._execute_tool("probe", {
            "text": "escape",
            "source_path": str(tmp_path / "outside.mp4"),
        })
    with pytest.raises(AgentBridgeError, match="control or credential files"):
        bridge._execute_tool("probe", {
            "text": "read config",
            "source_path": ".openmontage/agent_handoff.json",
        })


def test_stage_submission_cannot_claim_human_approval(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    bridge = _bridge(project, _ProbeTool(), monkeypatch)
    observed = {}

    def fake_submit(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {"status": "awaiting_human", "work_order": {"status": "awaiting_approval"}}

    monkeypatch.setattr(agent_mcp, "submit_manifest_stage", fake_submit)
    result = bridge.submit_stage({"brief": {}, "decision_log": {}}, status="completed")

    assert result["status"] == "awaiting_human"
    assert observed["args"][1:3] == ("agent-a", "idea")
    assert observed["kwargs"]["human_approved"] is False
    assert "approval_record" not in observed["kwargs"]


def test_mcp_tool_catalog_is_small_and_excludes_approval_inputs(tmp_path, monkeypatch) -> None:
    project = _project(tmp_path)
    bridge = _bridge(project, _ProbeTool(), monkeypatch)
    server = agent_mcp.OpenMontageMCPServer(bridge).server

    import asyncio

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    submit = next(tool for tool in tools if tool.name == "openmontage_submit_stage")

    assert names == {
        "openmontage_get_context",
        "openmontage_execute_tool",
        "openmontage_submit_stage",
    }
    assert "human_approved" not in submit.input_schema["properties"]

    context_result = asyncio.run(server.call_tool("openmontage_get_context", {}))
    assert not context_result.is_error
    assert context_result.structured_content["run_id"] == RUN_ID

    protected_result = asyncio.run(server.call_tool(
        "openmontage_execute_tool",
        {
            "tool_name": "probe",
            "inputs": {
                "text": "x",
                "source_path": "assets/video/source.mp4",
                "asset_request": {"sample_approval": {"status": "approved"}},
            },
        },
    ))
    assert protected_result.is_error
    assert "cannot set server-bound identity or approval fields" in protected_result.content[0].text


def test_codex_receives_only_scoped_ephemeral_mcp_configuration(tmp_path, monkeypatch) -> None:
    import tomllib

    from lib.local_director import command_for_director

    context = {
        "OPENMONTAGE_PROJECT_DIR": str(tmp_path / "project"),
        "OPENMONTAGE_PROJECT_ID": PROJECT_ID,
        "OPENMONTAGE_RUN_ID": RUN_ID,
        "OPENMONTAGE_AGENT_ID": "agent-a",
        "OPENMONTAGE_BACKLOT_URL": "http://127.0.0.1:8000",
        "OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN": "a-scoped-test-capability",
        "OPENMONTAGE_RUNTIME_ROOT": str(REPO_ROOT),
        "OPENMONTAGE_PROJECTS_DIR": str(tmp_path),
        "OPENAI_API_KEY": "must-never-enter-the-director-config",
    }
    argv, prompt = command_for_director(
        "codex",
        "codex.exe",
        "run the OpenMontage stage",
        working_directory=tmp_path / "project",
        mcp_environment=context,
    )
    overrides = [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "-c"]
    config = tomllib.loads("\n".join(overrides))
    server = config["mcp_servers"]["openmontage"]

    assert prompt == "run the OpenMontage stage"
    assert server["command"]
    assert server["args"] == ["-m", "lib.agent_mcp"]
    assert server["cwd"] == str(REPO_ROOT)
    assert server["env"]["OPENMONTAGE_RUN_ID"] == RUN_ID
    assert server["env"]["OPENMONTAGE_BACKLOT_URL"] == "http://127.0.0.1:8000"
    assert server["env"]["OPENMONTAGE_DIRECTOR_CAPABILITY_TOKEN"] == "a-scoped-test-capability"
    assert "OPENAI_API_KEY" not in server["env"]
    assert server["default_tools_approval_mode"] == "auto"
    assert server["enabled_tools"] == [
        "openmontage_get_context",
        "openmontage_execute_tool",
        "openmontage_submit_stage",
    ]


def test_mcp_server_loads_production_env_without_leaking_it_to_local_director(tmp_path, monkeypatch) -> None:
    from lib import local_director

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text(
        "OPENMONTAGE_TEST_PRODUCTION_KEY=not-a-real-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENMONTAGE_RUNTIME_ROOT", str(runtime))
    monkeypatch.delenv("OPENMONTAGE_TEST_PRODUCTION_KEY", raising=False)

    class FakeBridge:
        def _get_registry(self):
            assert os.environ.get("OPENMONTAGE_TEST_PRODUCTION_KEY") == "not-a-real-key"

    bridge = FakeBridge()
    monkeypatch.setattr(
        agent_mcp.LocalDirectorBridge,
        "from_environment",
        classmethod(lambda _cls: bridge),
    )

    class FakeKeeper:
        def __init__(self, _bridge):
            pass

        def start(self):
            pass

        def close(self):
            pass

    class FakeServer:
        def __init__(self, _bridge):
            assert os.environ.get("OPENMONTAGE_TEST_PRODUCTION_KEY") == "not-a-real-key"

        def run(self):
            assert "OPENMONTAGE_TEST_PRODUCTION_KEY" not in local_director.director_environment()

    monkeypatch.setattr(agent_mcp, "_LeaseKeeper", FakeKeeper)
    monkeypatch.setattr(agent_mcp, "OpenMontageMCPServer", FakeServer)

    assert agent_mcp.main() == 0


def test_invalid_run_capability_fails_before_loading_production_environment(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".env").write_text(
        "OPENMONTAGE_TEST_PRODUCTION_KEY=must-stay-unloaded\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENMONTAGE_RUNTIME_ROOT", str(runtime))
    monkeypatch.delenv("OPENMONTAGE_TEST_PRODUCTION_KEY", raising=False)

    def reject_before_loading(cls):
        raise AgentBridgeError("invalid run capability")

    monkeypatch.setattr(
        agent_mcp.LocalDirectorBridge,
        "from_environment",
        classmethod(reject_before_loading),
    )

    assert agent_mcp.main() == 2
    assert "OPENMONTAGE_TEST_PRODUCTION_KEY" not in os.environ
