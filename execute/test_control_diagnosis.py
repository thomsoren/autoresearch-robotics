"""Regression checks for the measured control and task facts behind the
LIBERO Goal task 0 diagnosis.

These pin down numbers that the written diagnosis relies on, so a later
controller or scene change cannot silently invalidate it. The simulator-backed
checks are skipped when LIBERO is not prepared; the rest are pure arithmetic.
"""

import numpy as np
import pytest

pytest.importorskip("inspect_robots_agent", reason="Install the execute dependency group")
from inspect_robots import Action, Observation  # noqa: E402

from execute.inspect_agent import LIMITS, LiberoEmbodiment  # noqa: E402

# Measured in MuJoCo 3.2.7 on libero_goal task 0, state 0. See the diagnosis in
# execute/DIAGNOSIS.md; regenerate with scripts under execute/diagnostics/ if the scene changes.
OSC_TRANSLATION_SCALE = 0.05
MEASURED_STEADY_GAIN = 0.254  # mean of 9 free-space sweeps; per-case spread <0.01
SUCCESS_QPOS_THRESHOLD = -0.14
MIDDLE_HANDLE_POS = np.array([0.0424, -0.1353, 1.0154])


class RecordingRobot:
    """Test plant with the diagnosed lag, exercised through production feedback."""

    gripper_command = -1.0
    success = False
    done = False

    def __init__(self, output):
        self.calls = []
        self.output = output
        self.position = np.zeros(3)
        self.steps = 0
        self.gain = MEASURED_STEADY_GAIN

    def pose(self):
        return self.position.copy(), np.eye(3)

    def _advance(self, action):
        self.calls.append(list(action))
        self.position += np.asarray(action[:3]) * OSC_TRANSLATION_SCALE * self.gain
        self.steps += 1

    def observe(self):
        return {"success": False, "done": False}


def make_body(tmp_path):
    body = LiberoEmbodiment(tmp_path, control="xyz")
    body.robot = RecordingRobot(tmp_path)
    body.observation = lambda raw: Observation()
    return body


def test_feedback_corrects_the_diagnosed_motion_shortfall(tmp_path):
    body = make_body(tmp_path)
    body.step(Action(np.array([0.01, 0.0, 0.0, 0.0])))
    assert body.robot.position[0] == pytest.approx(0.01, abs=0.001)
    assert len(body.robot.calls) > 1


def test_blocked_motion_stops_without_claiming_arrival(tmp_path):
    body = make_body(tmp_path)
    body.robot.gain = 0.0
    result = body.step(Action(np.array([0.01, 0.0, 0.0, 0.0])))
    assert result.info["stop_reason"] == "stalled"
    assert len(body.robot.calls) <= 30
    assert not body.last_motion["reached"]
    assert not result.terminated


def test_inspect_chunk_limit_caps_each_axis_at_one_centimetre():
    """Inspect splits move_by into <=1 cm chunks, before feedback executes each waypoint."""
    assert LIMITS[0] == LIMITS[1] == LIMITS[2] == 0.01
    assert LIMITS[3] == 1.0


def test_reaching_the_success_threshold_needs_far_more_than_one_chunk():
    """Opening needs >=14 cm of drawer travel, i.e. many chunks even when perfect."""
    travel = abs(SUCCESS_QPOS_THRESHOLD)
    perfect_chunks = travel / LIMITS[1]
    achieved_chunks = travel / (LIMITS[1] * MEASURED_STEADY_GAIN)
    assert perfect_chunks == pytest.approx(14.0)
    assert achieved_chunks > 55  # open-loop, the same pull costs 4x the steps


def test_gripper_command_sign_and_persistence(tmp_path):
    """Positive closes, negative opens, omitted retains the previous command."""
    body = make_body(tmp_path)
    body.step(Action(np.array([0.0, 0.0, 0.0, 0.5])))
    assert body.robot.calls[-1][-1] == 1.0
    body.step(Action(np.zeros(4)))
    assert body.robot.calls[-1][-1] == 1.0
    body.step(Action(np.array([0.0, 0.0, 0.0, -0.5])))
    assert body.robot.calls[-1][-1] == -1.0


def test_xyz_mode_holds_rotation(tmp_path):
    """Fixed wrist orientation is the adapter's deliberate restriction."""
    body = make_body(tmp_path)
    body.step(Action(np.array([0.01, 0.01, 0.01, 1.0])))
    assert body.robot.calls[-1][3:6] == [0.0, 0.0, 0.0]


def test_actions_beyond_the_declared_bounds_are_rejected(tmp_path):
    body = make_body(tmp_path)
    with pytest.raises(ValueError, match="exceeds declared bounds"):
        body.step(Action(np.array([0.02, 0.0, 0.0, 0.0])))


def test_success_threshold_and_handle_geometry_match_the_diagnosis(tmp_path):
    """Confirm the scene facts the approach analysis depends on."""
    name = "wooden_cabinet_1_middle_level"
    from simulation.sim import Robot

    robot = None
    try:
        robot = Robot(
            suite="libero_goal",
            task_id=0,
            init_state_id=0,
            seed=0,
            max_steps=5,
            output=str(tmp_path / "scene"),
            video=False,
            privileged=True,
        )
    except Exception as error:  # pragma: no cover - no usable renderer
        if robot is not None:
            robot.close()
        pytest.skip(f"LIBERO environment unavailable: {error}")

    try:
        sim = robot._env.env.sim
        model, data = sim.model, sim.data
        index = model.joint_name2id(name)
        assert list(np.round(model.jnt_range[index], 3)) == [-0.16, 0.01]

        address = model.jnt_qposadr[index]
        data.qpos[address] = SUCCESS_QPOS_THRESHOLD
        sim.forward()
        assert not robot._env.check_success(), "threshold is strict; -0.14 must not pass"
        data.qpos[address] = -0.16
        sim.forward()
        assert robot._env.check_success(), "full travel must satisfy the predicate"

        data.qpos[address] = 0.0
        sim.forward()
        handles = [
            g
            for g in range(model.ngeom)
            if (model.geom_id2name(g) or "") in ("wooden_cabinet_1_g28", "wooden_cabinet_1_g29")
        ]
        measured = np.mean([data.geom_xpos[g] for g in handles], axis=0)
        np.testing.assert_allclose(measured, MIDDLE_HANDLE_POS, atol=2e-3)
    finally:
        robot.close()
