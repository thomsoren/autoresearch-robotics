"""Test robot tool budgets without creating OpenGL contexts."""

import numpy as np
import pytest

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
