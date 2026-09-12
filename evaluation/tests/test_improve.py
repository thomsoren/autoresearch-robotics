"""Evidence boundaries and improvement output, without API calls or simulation."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from PIL import Image

pytest.importorskip("claude_agent_sdk", reason="Install the optional evaluation dependency group")
pytest.importorskip("dotenv")

from evaluation import improve  # noqa: E402


@pytest.fixture
def episode(tmp_path):
    path = tmp_path / "episode"
    path.mkdir()
    (path / "result.json").write_text(
        json.dumps(
            {
                "init_state_id": 0,
                "success": False,
                "policy": "noop",
                "error": None,
            }
        )
    )
    (path / "episode.json").write_text('{"instruction": "open the middle drawer"}')
    (path / "actions.jsonl").write_text('{"step": 1, "action": [0,0,0,0,0,0,0]}\n')
    (path / ".env").write_text("SHOULD_NOT_BE_COPIED=secret")
    Image.new("RGB", (8, 8), "red").save(path / "0001-agentview.png")
    return path


def report(decision="defer"):
    return {
        "decision": decision,
        "diagnosis": "No movement in the supplied actions.",
        "evidence": ["actions.jsonl:1"],
        "uncertainty": ["No executor trace."],
        "prediction": "Check progress after moving.",
        "candidate_name": "open_drawer" if decision == "propose" else "",
        "candidate_markdown": (
            "---\nname: open_drawer\ndescription: Open the drawer\n---\nObserve progress.\n"
            if decision == "propose"
            else ""
        ),
    }


def test_credential_mapping_is_local_to_sdk(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "executor-key")
    (tmp_path / ".env").write_text("CLAUDE_API_KEY=evaluation-key\n")
    key = improve.api_key(tmp_path)
    options = improve.sdk_options(
        key, tmp_path / "work", tmp_path / "config", "sonnet", 0.5, 8, {}, "test instructions"
    )
    assert options.env["ANTHROPIC_API_KEY"] == "evaluation-key"
    assert os.environ["ANTHROPIC_API_KEY"] == "executor-key"
    assert options.tools == options.skills == options.setting_sources == []
    assert options.strict_mcp_config
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert set(options.allowed_tools) == {
        "mcp__evidence__read_evidence",
        "mcp__evidence__view_frame",
    }
    monkeypatch.setenv("CLAUDE_API_KEY", "override")
    assert improve.api_key(tmp_path) == "override"


def test_snapshot_is_indexed_and_preserves_source(episode, tmp_path):
    output = tmp_path / "improve"
    manifest = improve.prepare_evidence(episode, output, "agent")
    assert manifest["fixture"]  # --kind agent cannot make a no-op eligible
    assert manifest["benchmark"]["success"] is False
    assert ".env" not in manifest["files_sha256"]
    assert any("No executor trace" in value for value in manifest["missing_evidence"])
    (episode / "actions.jsonl").write_text("changed after snapshot")
    text = improve.read_evidence(output, manifest["files_sha256"], "actions.jsonl")
    assert '1: {"step": 1' in text
    for name in ("../.env", ".env", "0001-agentview.png"):
        with pytest.raises(ValueError, match="indexed text"):
            improve.read_evidence(output, manifest["files_sha256"], name)
    with pytest.raises(FileExistsError):
        improve.prepare_evidence(episode, output, "smoke")


@pytest.mark.parametrize("state", [3, 7, -1, True])
def test_non_development_episode_rejected(episode, tmp_path, state):
    (episode / "result.json").write_text(json.dumps({"init_state_id": state, "success": False}))
    with pytest.raises(ValueError, match="development"):
        improve.prepare_evidence(episode, tmp_path / "output", "agent")
    assert not (tmp_path / "output").exists()


def test_output_cannot_modify_input_and_symlink_trace_rejected(episode, tmp_path):
    with pytest.raises(ValueError, match="outside"):
        improve.prepare_evidence(episode, episode / "output", "smoke")
    trace = tmp_path / "trace.jsonl"
    trace.symlink_to(episode / ".env")
    with pytest.raises(ValueError, match="symlinks"):
        improve.prepare_evidence(episode, tmp_path / "output", "smoke", trace=trace)


def test_image_is_delivered_as_image_content(episode, tmp_path):
    output = tmp_path / "output"
    manifest = improve.prepare_evidence(episode, output, "smoke")
    content = improve.frame_content(output, manifest["files_sha256"], "0001-agentview.png")
    assert content[1]["type"] == "image"
    assert content[1]["mimeType"] == "image/jpeg"
    assert content[1]["data"]
    with pytest.raises(ValueError, match="frame=0"):
        improve.frame_content(output, manifest["files_sha256"], "0001-agentview.png", 1)
    with pytest.raises(ValueError, match="indexed"):
        improve.frame_content(output, manifest["files_sha256"], "../secret.png")


@pytest.mark.parametrize("fixture,error", [(True, None), (False, "API unavailable")])
def test_fixtures_and_errors_cannot_propose(fixture, error):
    manifest = {"fixture": fixture, "benchmark": {"error": error}}
    improve.validate_report(report(), manifest)
    with pytest.raises(ValueError, match="cannot generate"):
        improve.validate_report(report("propose"), manifest)


@pytest.mark.parametrize(
    "name,markdown",
    [
        ("../../executor", "---\nname: ../../executor\ndescription: bad\n---\nedit"),
        ("open_drawer", "---\nname: other\ndescription: mismatch\n---\nedit"),
        ("open_drawer", "---\nname: open_drawer\ndescription: empty\n---\n"),
    ],
)
def test_invalid_candidate_rejected(name, markdown):
    candidate = report("propose")
    candidate.update(candidate_name=name, candidate_markdown=markdown)
    with pytest.raises(ValueError):
        improve.validate_report(candidate, {"fixture": False, "benchmark": {}})


def test_candidate_requires_explicit_proposal():
    candidate = report("propose")
    manifest = {"fixture": False, "benchmark": {}}
    improve.validate_report(candidate, manifest)
    candidate["decision"] = "defer"
    with pytest.raises(ValueError, match="must not contain"):
        improve.validate_report(candidate, manifest)


@pytest.mark.parametrize("sdk_error", [False, True])
def test_sdk_result_is_saved_without_promoting_or_leaking_key(
    episode,
    tmp_path,
    monkeypatch,
    sdk_error,
):
    output = tmp_path / "output"
    manifest = improve.prepare_evidence(episode, output, "smoke")

    class FakeClient:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            assert Path(self.options.cwd).is_dir()
            return self

        async def __aexit__(self, *_):
            pass

        async def query(self, prompt):
            assert '"fixture": true' in prompt
            assert "test-secret" not in prompt

        async def receive_response(self):
            yield improve.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=sdk_error,
                num_turns=1,
                session_id="test",
                total_cost_usd=0,
                result="Invalid API key test-secret" if sdk_error else "done",
                structured_output=None if sdk_error else report(),
            )

    monkeypatch.setattr(improve, "ClaudeSDKClient", FakeClient)
    if sdk_error:
        with pytest.raises(RuntimeError, match="Invalid API key") as error:
            asyncio.run(improve.run_agent(output, manifest, "test-secret"))
        assert "test-secret" not in str(error.value)
        assert not (output / "improvement.json").exists()
    else:
        result = asyncio.run(improve.run_agent(output, manifest, "test-secret"))
        assert result["decision"] == "defer"
        assert result["validation_status"] == "unvalidated"
        assert json.loads((output / "improvement.json").read_text())["decision"] == "defer"
    assert json.loads((output / "usage.json").read_text())["is_error"] is sdk_error
    assert not (output / "skills").exists()
    assert "test-secret" not in (output / "usage.json").read_text()
    assert (episode / "result.json").read_text() == (output / "evidence/result.json").read_text()
