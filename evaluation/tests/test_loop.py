"""Loop selection and failure boundaries using exported fixtures, not model claims."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")
pytest.importorskip("inspect_robots_agent")
from evaluation import loop  # noqa: E402
from evaluation.improve import prepare_evidence  # noqa: E402


def option(command, flag):
    return command[command.index(flag) + 1]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    code = {
        str(loop.ROOT / n): "a" * 64
        for n in (
            "simulation/sim.py",
            "evaluation/evaluate.py",
            "uv.lock",
            "execute/inspect_agent.py",
        )
    }
    monkeypatch.setattr(loop, "identity", lambda: code)
    protocol = {
        "model": "claude-opus-5",
        "base_prompt_sha256": "a" * 64,
        "executor_revision": "fixture",
        "executor_config_sha256": "c" * 64,
        "max_turns": 12,
        "max_api_requests": 12,
        "max_budget_usd": None,
        "timeout_seconds": 180,
        "fresh_session_per_episode": True,
    }
    monkeypatch.setattr(
        loop,
        "profile",
        lambda args: {
            "identity": code.copy(),
            "protocol": protocol,
            "base_prompt": "base prompt",
            "executor_config": {
                "model": "claude-opus-5",
                "effort": "low",
                "max_calls": 12,
                "max_steps": 300,
                "episode_timeout": 180,
                "suite": "libero_goal",
                "task_id": 0,
                "seed": 0,
                "control": "pose",
            },
        },
    )
    state = {"calls": [], "wins": [{0}, {0}, {0, 1}], "proposals": 0, "contexts": []}

    def child(command, log, timeout):
        assert timeout > 0
        state["calls"].append(command)
        output = Path(option(command, "--output"))
        if command[2] == "execute.inspect_agent":
            index = int(option(command, "--state"))
            # 1st/2nd/3rd calls belong to batch 0, then batch 1, etc.
            batch_number = (sum(c[2] == "execute.inspect_agent" for c in state["calls"]) - 1) // 3
            if state.get("timeout_batch") == batch_number:
                raise subprocess.TimeoutExpired(command, timeout)
            if state.get("missing_state") == index:
                return 1
            episode = output / f"state-{index:03d}"
            episode.mkdir(parents=True)
            for camera in ("agentview", "robot0_eye_in_hand"):
                (episode / f"0000-{camera}.png").write_bytes(
                    f"frame-{index}-{batch_number if state.get('frame_mismatch') else 0}".encode()
                )
            text = ""
            if "--skill" in command:
                text = Path(option(command, "--skill")).read_text()
                skill = output / "skills/task/SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(text)
            fingerprint = loop.skill_hash(output / "skills" if text else None)
            error = "API unavailable" if state.get("api_errors") else None
            row = {
                "suite": "libero_goal",
                "task_id": 0,
                "init_state_id": index,
                "seed": 100 + index + (batch_number if state.get("seed_mismatch") else 0),
                "max_steps": 300,
                "observation_mode": "calibrated_rgbd",
                "control": "pose",
                "policy": "inspect-robots-agent",
                "skill_hash": fingerprint,
                "success": index in state["wins"][batch_number] and error is None,
                "steps": 5,
                "error": error,
                "api_requests_attempted": 1,
                "libero_revision": "fixture",
                "settle_steps": 10,
                "task_name": "drawer",
            }
            loop.save(episode / "result.json", row)
            (episode / "actions.jsonl").write_text("{}\n")
            loop.save(
                output / "experiment.json",
                {
                    "model": "claude-opus-5",
                    "speed": "standard",
                    "effort": "low",
                    "max_api_requests": 12,
                    "max_steps": 300,
                    "timeout_seconds": 180,
                    "suite": "libero_goal",
                    "task_id": 0,
                    "seed": 0,
                    "state_id": index,
                    "control": "pose",
                    "rotation": "rot6d",
                    "smoke": False,
                    "skill_hash": fingerprint,
                    "adapter_sha256": "a" * 64,
                },
            )
            prompt = "base prompt" + ("\nnotes: " + text if text else "")
            if state.get("omit_skill") and text:
                prompt = "base prompt"
            loop.save(output / "executor-trace.json", [{"role": "system", "content": prompt}])
            return 1 if error else 0
        assert command[2] == "evaluation.improve"
        state["proposals"] += 1
        context = Path(option(command, "--context"))
        state["contexts"].append(loop.read(context))
        prepare_evidence(
            Path(command[3]),
            output,
            "agent",
            trace=Path(option(command, "--trace")),
            skills=Path(option(command, "--skills")) if "--skills" in command else None,
            context=context,
            incumbent=(Path(option(command, "--incumbent")) if "--incumbent" in command else None),
            regression=(
                Path(option(command, "--regression")) if "--regression" in command else None
            ),
        )
        deferred = state.get("defer", False)
        text = (
            "---\nname: robot_operating_guide\ndescription: test skill\n---\n"
            f"Revision {state['proposals']}.\n"
        )
        report = {
            "decision": "defer" if deferred else "propose",
            "diagnosis": "fixture",
            "evidence": ["result.json:1"],
            "uncertainty": [],
            "prediction": "inspect progress",
            "candidate_name": "" if deferred else "robot_operating_guide",
            "candidate_markdown": "" if deferred else text,
            "validation_status": "unvalidated",
        }
        loop.save(output / "improvement.json", report)
        if not deferred:
            path = output / "skills/robot_operating_guide/SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("tampered" if state.get("tamper_skill") else text)
        return 0

    monkeypatch.setattr(loop, "child", child)
    state["args"] = loop.arguments(
        [
            "--output",
            str(tmp_path / "loop"),
            "--iterations",
            "2",
            "--model",
            "claude-opus-5",
            "--max-steps",
            "300",
            "--episode-timeout",
            "180",
        ]
    )
    return state


def test_pose_feedback_loop_defaults_allow_bounded_waypoints(tmp_path):
    args = loop.arguments(["--output", str(tmp_path / "loop")])
    assert args.model == "claude-fable-5-1"
    assert args.control == "pose"
    assert args.max_steps == 900
    assert args.episode_timeout == 300


def test_rejected_candidate_informs_next_revision_but_only_strict_gain_is_selected(rig):
    result = loop.run(rig["args"])
    assert [c["decision"] for c in result["iterations"]] == ["reject", "keep"]
    assert result["selected_batch"].endswith("iteration-002/candidate")
    assert Path(result["selected_skill"]).read_text().endswith("Revision 2.\n")
    second = rig["contexts"][1]
    assert second["tested_batch"].endswith("iteration-001/candidate")
    assert second["selection_history"]["selected_batch"].endswith("baseline")
    assert second["selection_history"]["iterations"][0]["decision"] == "reject"
    assert result["active_skills_updated"] is False
    batches = [c for c in rig["calls"] if c[2] == "execute.inspect_agent"]
    assert [int(option(c, "--state")) for c in batches] == [0, 1, 2] * 3
    assert all(option(c, "--control") == "pose" for c in batches)
    assert len({option(c, "--output") for c in batches}) == 9  # Fresh child per episode.
    manifest, summary, rows = loop.load_batch(Path(result["selected_batch"]))
    assert manifest["seed"] == 0 and manifest["episode_seeds"] == {"0": 100, "1": 101, "2": 102}
    assert summary["successes"] == 2
    assert rows[0]["seed"] == 100  # Original derived seed is not rewritten.
    assert manifest["control"] == "pose"
    assert manifest["evaluation_split"] == rows[0]["evaluation_split"] == "development"


def test_rejected_candidate_is_separate_from_selected_incumbent_and_regression(rig, tmp_path):
    initial = tmp_path / "initial.md"
    initial.write_text("---\nname: initial\ndescription: initial skill\n---\nObserve.\n")
    rig["args"].skill = initial
    rig["wins"] = [{0}, {0}, {0, 1}]

    loop.run(rig["args"])

    improvements = [c for c in rig["calls"] if c[2] == "evaluation.improve"]
    assert option(improvements[1], "--skills").endswith("iteration-001/candidate/skills")
    assert option(improvements[1], "--incumbent").endswith("baseline/skills")
    assert option(improvements[1], "--regression").endswith("baseline/state-000")
    evidence = loop.read(rig["args"].output / "iteration-002/improvement/evidence.json")
    assert "skills/task/SKILL.md" in evidence["files_sha256"]
    assert "incumbent/task/SKILL.md" in evidence["files_sha256"]
    assert evidence["regression"]["success"] is True


def test_tie_retains_baseline_and_stops_at_iteration_limit(rig):
    rig["wins"] = [{0}, {0}, {0}]
    result = loop.run(rig["args"])
    assert result["status"] == "iteration_limit"
    assert result["selected_batch"].endswith("baseline")
    assert result["selected_skill"] is None


def test_later_regression_keeps_the_previously_selected_skill(rig):
    rig["wins"] = [{0}, {0, 1}, {0}]
    result = loop.run(rig["args"])
    assert [c["decision"] for c in result["iterations"]] == ["keep", "reject"]
    assert result["selected_batch"].endswith("iteration-001/candidate")
    assert Path(result["selected_skill"]).read_text().endswith("Revision 1.\n")


def test_api_failures_count_as_failed_attempts_and_do_not_generate_task_skills(rig):
    rig["api_errors"] = True
    result = loop.run(rig["args"])
    assert result["status"] == "deferred" and rig["proposals"] == 0
    summary = loop.load_batch(Path(result["selected_batch"]))[1]
    assert summary["episodes"] == summary["policy_errors"] == 3
    assert summary["successes"] == 0


@pytest.mark.parametrize("failure", ["omit_skill", "tamper_skill"])
def test_invalid_skill_cannot_be_selected(rig, failure):
    rig[failure] = True
    with pytest.raises(ValueError):
        loop.run(rig["args"])
    result = loop.read(rig["args"].output / "loop.json")
    assert result["status"] == "error" and result["selected_batch"].endswith("baseline")
    assert result["selected_skill"] is None


def test_incomplete_baseline_is_never_compared(rig):
    rig["missing_state"] = 1
    with pytest.raises(RuntimeError, match="did not export"):
        loop.run(rig["args"])
    result = loop.read(rig["args"].output / "loop.json")
    assert result["selected_batch"] is None and rig["proposals"] == 0
    assert not list(rig["args"].output.rglob("comparison.json"))


def test_timeout_during_candidate_preserves_incumbent(rig):
    rig["timeout_batch"] = 1
    result = loop.run(rig["args"])
    assert result["status"] == "time_limit"
    assert result["selected_batch"].endswith("baseline")
    assert result["iterations"] == []


def test_seed_mismatch_is_inconclusive_even_with_higher_success(rig):
    rig["wins"] = [{0}, {0, 1}]
    rig["seed_mismatch"] = True
    result = loop.run(rig["args"])
    assert result["status"] == "inconclusive"
    assert result["selected_batch"].endswith("baseline")
    assert "Different episode_seeds" in result["iterations"][0]["reasons"]


def test_changed_starting_images_cannot_claim_skill_improvement(rig):
    rig["wins"] = [{0}, {0, 1}]
    rig["frame_mismatch"] = True
    result = loop.run(rig["args"])
    assert result["status"] == "inconclusive"
    assert result["selected_batch"].endswith("baseline")
    assert "Different initial camera frames" in result["iterations"][0]["reasons"]


def test_different_control_contract_cannot_claim_skill_improvement(rig):
    rig["args"].iterations = 1
    loop.run(rig["args"])
    candidate = rig["args"].output / "iteration-001/candidate"
    for name in ("manifest.json", "summary.json"):
        path = candidate / name
        data = loop.read(path)
        data["control"] = "xyz"
        loop.save(path, data)

    result = loop.compare_runs(
        rig["args"].output / "baseline",
        candidate,
        output=rig["args"].output / "control-comparison.json",
    )

    assert result["decision"] == "inconclusive"
    assert "Different control" in result["reasons"]


def test_defer_stops_without_candidate_rollouts(rig):
    rig["defer"] = True
    result = loop.run(rig["args"])
    assert result["status"] == "deferred"
    assert len(rig["calls"]) == 4


def test_not_enough_time_does_not_start_or_shorten_an_episode(rig, monkeypatch):
    monkeypatch.setattr(loop.time, "monotonic", lambda: 0)
    rig["args"].max_seconds = 100
    result = loop.run(rig["args"])
    assert result["status"] == "time_limit" and not rig["calls"]


def test_child_timeout_terminates_process(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        loop.child(
            [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "child.log", 0.1
        )


def test_no_overwrite_of_completed_loop(rig):
    rig["defer"] = True
    loop.run(rig["args"])
    before = (rig["args"].output / "loop.json").read_bytes()
    with pytest.raises(FileExistsError):
        loop.run(rig["args"])
    assert (rig["args"].output / "loop.json").read_bytes() == before
