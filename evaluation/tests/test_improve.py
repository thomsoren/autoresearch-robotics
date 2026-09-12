"""Evidence boundaries and improvement output, without API calls or simulation."""

import asyncio
import hashlib
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


# --- Deterministic derived facts -------------------------------------------------
# The traces below are synthetic fixtures: hand-written messages with known
# arithmetic, used to prove the extractor's numbers. They are not recorded episodes.


def observation(position):
    text = (
        "Current observation.\nInstruction: open the middle drawer of the cabinet\n"
        f"state[eef_pos]: [{position[0]}, {position[1]}, {position[2]}]\n"
        "state[finger_qpos]: [0.0387, -0.0387]"
    )
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def call(name, deltas=None, double_encoded=False):
    arguments = json.dumps({"deltas": deltas} if deltas is not None else {})
    if double_encoded:  # The real adapter has produced a doubly encoded payload.
        arguments = json.dumps(arguments)
    function = {"name": name, "arguments": arguments}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "t", "type": "function", "function": function}],
    }


def synthetic_trace():
    """Two moves achieving a 0.20 ratio, one zero-norm move, then give_up."""
    return [
        {"role": "system", "content": "You are controlling a robot."},
        {"role": "user", "content": "Goal: open the middle drawer of the cabinet"},
        observation([0.0, 0.0, 1.0]),
        call("move_by", {"dy": -0.1}),
        observation([0.0, -0.02, 1.0]),
        call("move_by", {"dz": -0.05}, double_encoded=True),
        observation([0.0, -0.02, 0.99]),
        call("move_by", {"dx": 0.0}),
        observation([0.0, -0.02, 0.99]),
        call("give_up"),
    ]


@pytest.fixture
def inspect_episode(tmp_path):
    path = tmp_path / "inspect-episode"
    path.mkdir()
    (path / "result.json").write_text(
        json.dumps(
            {
                "init_state_id": 0,
                "success": False,
                "policy": "inspect-robots-agent",
                "error": None,
                "steps": 3,
            }
        )
    )
    (path / "episode.json").write_text('{"instruction": "open the middle drawer"}')
    rows = [{"step": i + 1, "action": [0, -0.1, 0, 0, 0, 0, -1.0]} for i in range(3)]
    (path / "actions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (path / "executor-trace.json").write_text(json.dumps(synthetic_trace()))
    Image.new("RGB", (8, 8), "blue").save(path / "0001-agentview.png")
    return path


def facts_for(episode, trace=True):
    result = json.loads((episode / "result.json").read_text())
    path = episode / "executor-trace.json" if trace else None
    return improve.derive_facts(episode, path, result)


def test_commanded_and_achieved_are_paired_with_ratios(inspect_episode):
    moves = facts_for(inspect_episode)["motion"]["calls"]
    assert [m["ratio"] for m in moves] == [0.2, 0.2, None]
    assert moves[0]["commanded"] == [0.0, -0.1, 0.0]
    assert moves[0]["achieved"] == [0.0, -0.02, 0.0]
    assert moves[0]["tool"] == "move_by"


def test_tool_inventory_counts_give_up(inspect_episode):
    inventory = facts_for(inspect_episode)["tool_calls"]
    assert inventory["total"] == 4
    assert inventory["counts"] == {"move_by": 3, "give_up": 1}


def test_open_gripper_throughout_is_reported_as_no_close(inspect_episode):
    gripper = facts_for(inspect_episode)["gripper"]
    assert gripper["close_ever_commanded"] is False
    assert gripper["distinct_commands"] == [-1.0]
    assert gripper["rows"] == 3


def test_close_command_is_detected(inspect_episode):
    rows = [
        {"step": 1, "action": [0, 0, 0, 0, 0, 0, -1.0]},
        {"step": 2, "action": [0, 0, 0, 0, 0, 0, 1.0]},
    ]
    (inspect_episode / "actions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    gripper = facts_for(inspect_episode)["gripper"]
    assert gripper["close_ever_commanded"] is True
    assert gripper["distinct_commands"] == [-1.0, 1.0]


def test_missing_trace_keeps_gripper_facts_and_explains_motion(inspect_episode):
    facts = facts_for(inspect_episode, trace=False)
    assert facts["gripper"]["rows"] == 3
    assert facts["motion"]["reason"]
    assert "calls" not in facts["motion"]


def test_malformed_trace_does_not_raise(inspect_episode):
    (inspect_episode / "executor-trace.json").write_text("this is not json{{{")
    facts = facts_for(inspect_episode)
    assert facts["motion"]["reason"]
    assert facts["gripper"]["rows"] == 3


def test_other_executor_policy_is_not_parsed_as_inspect(inspect_episode):
    result = json.loads((inspect_episode / "result.json").read_text())
    result["policy"] = "noop"
    facts = improve.derive_facts(inspect_episode, inspect_episode / "executor-trace.json", result)
    assert facts["motion"]["reason"] == "unsupported_policy"
    assert facts["gripper"]["rows"] == 3


def test_unreadable_actions_do_not_raise(inspect_episode):
    (inspect_episode / "actions.jsonl").write_text("not jsonl at all\n")
    facts = facts_for(inspect_episode)
    assert facts["gripper"]["reason"]
    assert facts["motion"]["calls"]


def test_derived_facts_are_indexed_evidence(inspect_episode, tmp_path):
    output = tmp_path / "improve"
    manifest = improve.prepare_evidence(
        inspect_episode,
        output,
        "agent",
        trace=inspect_episode / "executor-trace.json",
    )
    assert "derived-facts.json" in manifest["files_sha256"]
    text = improve.read_evidence(output, manifest["files_sha256"], "derived-facts.json")
    assert "ratio" in text
    headline = manifest["derived_facts"]
    assert headline["close_ever_commanded"] is False
    assert headline["tool_call_counts"] == {"move_by": 3, "give_up": 1}
    assert headline["ratio_range"] == [0.2, 0.2]


def test_derived_facts_digest_matches_written_file(inspect_episode, tmp_path):
    output = tmp_path / "improve"
    manifest = improve.prepare_evidence(
        inspect_episode,
        output,
        "agent",
        trace=inspect_episode / "executor-trace.json",
    )
    written = (output / "evidence" / "derived-facts.json").read_bytes()
    assert hashlib.sha256(written).hexdigest() == manifest["files_sha256"]["derived-facts.json"]


def test_unavailable_motion_facts_are_listed_as_missing_evidence(inspect_episode, tmp_path):
    manifest = improve.prepare_evidence(inspect_episode, tmp_path / "improve", "agent")
    assert any("commanded" in value.lower() for value in manifest["missing_evidence"])
