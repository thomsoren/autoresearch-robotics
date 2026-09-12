"""Test evaluator integrity and tool budgets without creating OpenGL contexts."""

import json
from pathlib import Path

import numpy as np
import pytest

from simulation import evaluate
from simulation.sim import Robot


def bare_robot():
    robot = Robot.__new__(Robot)
    robot.steps, robot.max_steps, robot.success = 0, 3, False
    robot.gripper_command = -1.0
    robot.observe = lambda: {"steps": robot.steps, "done": robot.done}
    robot._advance = lambda action: setattr(robot, "steps", robot.steps + 1)
    return robot


@pytest.mark.parametrize(
    "action", [[0] * 6, [0] * 8, [float("nan")] * 7, [float("inf")] * 7, [2] * 7]
)
def test_rejects_invalid_actions_before_motion(action):
    robot = bare_robot()
    with pytest.raises(ValueError):
        robot.step(action)
    assert robot.steps == 0


@pytest.mark.parametrize("repeat", [0, -1, 101, 1.5, True])
def test_rejects_invalid_repeat(repeat):
    with pytest.raises(ValueError):
        bare_robot().step([0] * 7, repeat=repeat)


def test_repeated_actions_cannot_exceed_episode_budget():
    robot = bare_robot()
    assert robot.step([0] * 7, repeat=100) == {"steps": 3, "done": True}
    robot.step([0] * 7)
    assert robot.steps == 3


def test_success_stops_actions_immediately():
    robot = bare_robot()
    robot._advance = lambda action: setattr(robot, "success", True)
    robot.step([0] * 7, repeat=100)
    assert robot.success and robot.steps == 0


def test_move_to_reports_unreachable_target():
    robot = bare_robot()
    robot._obs = {"robot0_eef_pos": np.zeros(3)}
    result = robot.move_to([1, 0, 0])
    assert result["reached"] is False
    assert result["steps"] == 3


class FakeRobot:
    def __init__(self, output, init_state_id, **kwargs):
        self.output = Path(output)
        self.output.mkdir()
        self.state_id = init_state_id

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def result(self):
        return {"success": False, "steps": 1, "init_state_id": self.state_id}


def test_policy_claim_cannot_set_success_and_errors_are_counted(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluate, "Robot", FakeRobot)

    def policy(robot, skills):
        if robot.state_id == 1:
            raise RuntimeError("agent unavailable")
        return {"success": True}

    summary = evaluate.evaluate(policy, tmp_path / "eval", state_ids=[0, 1])
    assert summary["episodes"] == 2
    assert summary["success_rate"] == 0
    assert summary["policy_errors"] == 1
    rows = [json.loads(line) for line in (tmp_path / "eval/results.jsonl").read_text().splitlines()]
    assert rows[1]["termination"] == "policy_error"


def test_skill_changes_invalidate_eval(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluate, "Robot", FakeRobot)
    source = tmp_path / "source"
    source.mkdir()
    (source / "skill.md").write_text("baseline")

    def policy(robot, skills):
        (skills / "skill.md").write_text("modified")

    with pytest.raises(RuntimeError, match="modified the skill"):
        evaluate.evaluate(policy, tmp_path / "eval", skill_dir=source, state_ids=[0])
    assert (source / "skill.md").read_text() == "baseline"


def test_python_bytecode_does_not_invalidate_skill_content(tmp_path):
    (tmp_path / "skill.py").write_text("def grasp(): pass")
    before = evaluate.skill_hash(tmp_path)
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "skill.cpython-311.pyc").write_bytes(b"bytecode")
    assert evaluate.skill_hash(tmp_path) == before
