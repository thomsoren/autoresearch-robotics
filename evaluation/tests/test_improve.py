"""Evidence boundaries and improvement output, without API calls or simulation."""

import asyncio
import hashlib
import json
import os
import sys
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
        "candidate_name": "robot_operating_guide" if decision == "propose" else "",
        "candidate_markdown": (
            "---\nname: robot_operating_guide\n"
            "description: General feedback operating guide\n---\n"
            "Observe the scene, act in bounded increments, and verify measured progress.\n"
            if decision == "propose"
            else ""
        ),
    }


def test_program_requests_reusable_guidance_from_supplied_tool_docs():
    prompt = (Path(improve.__file__).with_name("program.md")).read_text()
    assert "one general `robot_operating_guide`" in prompt
    assert "Read the supplied executor tool documentation" in prompt
    assert "Do not copy fixed world coordinates or a fixed trajectory" in prompt
    assert "single absolute target can be split internally" in prompt
    assert "Do not prescribe a waypoint count" in prompt
    assert "Absence of a loaded guide is only a failure hypothesis" in prompt
    assert "Do not include code examples" in prompt
    assert "successful regression" in prompt
    assert "regression/executor-trace.json" in prompt
    assert "use only its move_by, done and give_up tools" not in prompt
    assert "adapter fixes wrist orientation" not in prompt


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


def test_control_evidence_is_included_in_immutable_snapshot(episode, tmp_path):
    control = episode / "control.jsonl"
    control.write_text(
        '{"request":{"position":[0.1,0.2,0.3]},"achieved":{"position":[0.09,0.2,0.3]}}\n'
    )
    output = tmp_path / "output"

    manifest = improve.prepare_evidence(episode, output, "agent")

    digest = manifest["files_sha256"]["control.jsonl"]
    assert digest == hashlib.sha256(control.read_bytes()).hexdigest()
    control.write_text("changed after snapshot\n")
    assert (output / "evidence/control.jsonl").read_text().startswith('{"request"')


def test_successful_development_regression_is_indexed_separately(episode, tmp_path):
    regression = tmp_path / "regression"
    regression.mkdir()
    (regression / "result.json").write_text(
        json.dumps({"init_state_id": 2, "success": True, "policy": "inspect-robots-agent"})
    )
    (regression / "episode.json").write_text('{"instruction": "open the middle drawer"}')
    (regression / "actions.jsonl").write_text('{"step": 1}\n')
    (regression / "control.jsonl").write_text('{"status": "reached"}\n')
    (regression / "executor-trace.json").write_text('[{"role":"assistant","tool":"move_to"}]\n')

    manifest = improve.prepare_evidence(
        episode, tmp_path / "output", "agent", regression=regression
    )

    assert manifest["regression"]["init_state_id"] == 2
    assert manifest["regression"]["success"] is True
    assert "regression/control.jsonl" in manifest["files_sha256"]
    assert "regression/executor-trace.json" in manifest["files_sha256"]
    assert "regression/result.json" in manifest["files_sha256"]


def test_tested_and_incumbent_skill_snapshots_remain_distinct(episode, tmp_path):
    tested = tmp_path / "tested"
    incumbent = tmp_path / "incumbent"
    (tested / "drawer").mkdir(parents=True)
    (incumbent / "drawer").mkdir(parents=True)
    (tested / "drawer/SKILL.md").write_text("tested candidate\n")
    (incumbent / "drawer/SKILL.md").write_text("selected incumbent\n")

    manifest = improve.prepare_evidence(
        episode,
        tmp_path / "output",
        "agent",
        tested,
        None,
        None,
        None,
        incumbent,
    )

    assert manifest["tested_skill_files"] == ["skills/drawer/SKILL.md"]
    assert manifest["incumbent_skill_files"] == ["incumbent/drawer/SKILL.md"]
    assert (tmp_path / "output/evidence/skills/drawer/SKILL.md").read_text() == "tested candidate\n"
    assert (
        tmp_path / "output/evidence/incumbent/drawer/SKILL.md"
    ).read_text() == "selected incumbent\n"


def test_cli_appends_regression_and_incumbent_inputs(episode, tmp_path, monkeypatch):
    captured = {}

    def prepare(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {}

    output = tmp_path / "output"
    regression = tmp_path / "regression"
    incumbent = tmp_path / "incumbent"
    monkeypatch.setattr(improve, "prepare_evidence", prepare)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "improve",
            str(episode),
            "--prepare-only",
            "--output",
            str(output),
            "--regression",
            str(regression),
            "--incumbent",
            str(incumbent),
        ],
    )

    improve.main()

    assert captured["args"][:3] == (episode, output.resolve(), "smoke")
    assert captured["kwargs"] == {"regression": regression, "incumbent": incumbent}


@pytest.mark.parametrize(
    "state,success,message",
    [(3, True, "development"), (7, True, "development"), (0, False, "success true")],
)
def test_regression_must_be_a_successful_development_episode(
    episode, tmp_path, state, success, message
):
    regression = tmp_path / "regression"
    regression.mkdir()
    (regression / "result.json").write_text(
        json.dumps({"init_state_id": state, "success": success})
    )

    with pytest.raises(ValueError, match=message):
        improve.prepare_evidence(episode, tmp_path / "output", "agent", regression=regression)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("evaluation_split", ["held_out", "frozen_evaluation"])
def test_regression_rejects_explicit_reserved_split(episode, tmp_path, evaluation_split):
    regression = tmp_path / "regression"
    regression.mkdir()
    (regression / "result.json").write_text(
        json.dumps(
            {
                "init_state_id": 0,
                "success": True,
                "policy": "inspect-robots-agent",
                "evaluation_split": evaluation_split,
            }
        )
    )

    with pytest.raises(ValueError, match="development split"):
        improve.prepare_evidence(episode, tmp_path / "output", "agent", regression=regression)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "policy,error",
    [
        ("noop", None),
        ("inspect-robots-agent", "controller crashed"),
        ("inspect-robots-agent", ""),
    ],
)
def test_regression_rejects_fixtures_and_errors(episode, tmp_path, policy, error):
    regression = tmp_path / "regression"
    regression.mkdir()
    (regression / "result.json").write_text(
        json.dumps(
            {
                "init_state_id": 0,
                "success": True,
                "policy": policy,
                "error": error,
                "evaluation_split": "development",
            }
        )
    )

    with pytest.raises(ValueError, match="error-free agent episode"):
        improve.prepare_evidence(episode, tmp_path / "output", "agent", regression=regression)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("state", [3, 7, -1, True])
def test_non_development_episode_rejected(episode, tmp_path, state):
    (episode / "result.json").write_text(json.dumps({"init_state_id": state, "success": False}))
    with pytest.raises(ValueError, match="development"):
        improve.prepare_evidence(episode, tmp_path / "output", "agent")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("evaluation_split", ["held_out", "frozen_evaluation"])
def test_reserved_split_is_rejected_even_for_development_state_id(
    episode, tmp_path, evaluation_split
):
    (episode / "result.json").write_text(
        json.dumps(
            {
                "init_state_id": 0,
                "success": False,
                "evaluation_split": evaluation_split,
            }
        )
    )

    with pytest.raises(ValueError, match="development split"):
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
        ("robot_operating_guide", "---\nname: other\ndescription: mismatch\n---\nedit"),
        (
            "robot_operating_guide",
            "---\nname: robot_operating_guide\ndescription: empty\n---\n",
        ),
    ],
)
def test_invalid_candidate_rejected(name, markdown):
    candidate = report("propose")
    candidate.update(candidate_name=name, candidate_markdown=markdown)
    with pytest.raises(ValueError):
        improve.validate_report(candidate, {"fixture": False, "benchmark": {}})


def test_candidate_markdown_has_a_16000_character_limit():
    candidate = report("propose")
    prefix = "---\nname: robot_operating_guide\ndescription: bounded guide\n---\n"
    candidate["candidate_markdown"] = prefix + "x" * (16000 - len(prefix))
    manifest = {"fixture": False, "benchmark": {}}
    improve.validate_report(candidate, manifest)

    candidate["candidate_markdown"] += "x"
    with pytest.raises(ValueError, match="16000"):
        improve.validate_report(candidate, manifest)


def test_proposal_must_be_the_general_robot_operating_guide():
    candidate = report("propose")
    candidate["candidate_name"] = "open_middle_drawer"
    candidate["candidate_markdown"] = candidate["candidate_markdown"].replace(
        "robot_operating_guide", "open_middle_drawer"
    )
    with pytest.raises(ValueError, match="robot_operating_guide"):
        improve.validate_report(candidate, {"fixture": False, "benchmark": {}})


@pytest.mark.parametrize(
    "recipe",
    [
        "```\nmove_to(targets={x: 0.1})\n```",
        "Begin at the remembered world pose [-0.22, +0.01, 1.17].",
        "Move to the cabinet at world y = -0.25.",
    ],
)
def test_proposal_rejects_code_and_numeric_coordinate_recipes(recipe):
    candidate = report("propose")
    candidate["candidate_markdown"] += recipe

    with pytest.raises(ValueError, match="code or coordinate"):
        improve.validate_report(candidate, {"fixture": False, "benchmark": {}})


def test_generic_natural_language_operating_guide_is_valid():
    candidate = report("propose")
    candidate["candidate_markdown"] += (
        "Locate the task-relevant object from fresh observations. Approach in bounded "
        "increments, compare requested and achieved motion, and stop after one failed "
        "recovery. Use the current executor documentation for available actions and limits.\n"
    )

    improve.validate_report(candidate, {"fixture": False, "benchmark": {}})


def test_candidate_requires_explicit_proposal():
    candidate = report("propose")
    manifest = {"fixture": False, "benchmark": {}}
    improve.validate_report(candidate, manifest)
    candidate["decision"] = "defer"
    with pytest.raises(ValueError, match="must not contain"):
        improve.validate_report(candidate, manifest)


def test_regression_hash_is_checked_after_diagnosis(episode, tmp_path, monkeypatch):
    regression = tmp_path / "regression"
    regression.mkdir()
    (regression / "result.json").write_text(
        json.dumps({"init_state_id": 1, "success": True, "policy": "inspect-robots-agent"})
    )
    (regression / "control.jsonl").write_text('{"status": "reached"}\n')
    output = tmp_path / "output"
    manifest = improve.prepare_evidence(episode, output, "smoke", regression=regression)

    class FakeClient:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def query(self, _prompt):
            pass

        async def receive_response(self):
            (output / "evidence/regression/control.jsonl").write_text("mutated\n")
            yield improve.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="test",
                total_cost_usd=0,
                result="done",
                structured_output=report(),
            )

    monkeypatch.setattr(improve, "ClaudeSDKClient", FakeClient)
    with pytest.raises(RuntimeError, match="Evidence changed"):
        asyncio.run(improve.run_agent(output, manifest, "test-secret"))


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
