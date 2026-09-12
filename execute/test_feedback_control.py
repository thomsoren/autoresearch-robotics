"""Behavior checks against real LIBERO physics; no model or privileged task actions."""

import json

import numpy as np
import pytest
from inspect_robots import Action, Scene
from scipy.spatial.transform import Rotation

from execute.inspect_agent import LiberoEmbodiment


def test_centimeter_command_reaches_within_two_millimeters(tmp_path):
    body = LiberoEmbodiment(tmp_path, max_steps=100, control="xyz")
    try:
        before = body.reset(Scene(id="test", instruction="control check"))
        body.step(Action(np.array([0, 0, 0.01, 0])))
        after = body.robot.observe()
        error = abs(after["eef_pos"][2] - before.state["eef_pos"][2] - 0.01)
        assert error < 0.002, f"commanded 10 mm; residual {error * 1000:.2f} mm"
    finally:
        body.close()


def test_observation_quaternion_matches_position_control_site(tmp_path):
    body = LiberoEmbodiment(tmp_path, max_steps=10)
    try:
        body.reset(Scene(id="test", instruction="control check"))
        arm = body.robot._env.env.robots[0]
        site = arm.sim.data.site_xmat[arm.eef_site_id].reshape(3, 3)
        reported = Rotation.from_quat(body.robot.observe()["eef_quat_xyzw"]).as_matrix()
        np.testing.assert_allclose(reported, site, atol=1e-6)
    finally:
        body.close()


def test_rotation_holds_position_and_records_physics_budget(tmp_path):
    from execute.control import servo_pose

    body = LiberoEmbodiment(tmp_path, max_steps=100, control="xyz")
    try:
        body.reset(Scene(id="test", instruction="control check"))
        robot = body.robot
        position, rotation = robot.pose()
        target = Rotation.from_rotvec([0.25, 0, 0]).as_matrix() @ rotation
        result = servo_pose(robot, position, target, max_steps=80)
        assert result["reached"]
        assert result["position_error"] < 0.002
        assert result["rotation_error"] < 0.03
        record = json.loads((robot.output / "control.jsonl").read_text().splitlines()[-1])
        assert record["end_step"] == robot.steps
        assert record["end_step"] > record["start_step"]
        assert len((robot.output / "actions.jsonl").read_text().splitlines()) == robot.steps
    finally:
        body.close()


def test_unreached_target_cannot_overrun_episode_budget(tmp_path):
    from execute.control import servo_pose

    body = LiberoEmbodiment(tmp_path, max_steps=3)
    try:
        body.reset(Scene(id="test", instruction="control check"))
        position, rotation = body.robot.pose()
        result = servo_pose(body.robot, position + [0, 0, 0.3], rotation, max_steps=80)
        assert body.robot.steps == 3
        assert not result["reached"]
        assert result["stop_reason"] == "episode_limit"
        servo_pose(body.robot, position, rotation)
        assert body.robot.steps == 3
    finally:
        body.close()


def test_degenerate_orientation_rejected_before_physics(tmp_path):
    from execute.control import rotation_from_6d

    for values in ([0] * 6, [1, 0, 0, 2, 0, 0], [float("nan")] * 6):
        with pytest.raises(ValueError):
            rotation_from_6d(values)


def test_interpolation_cannot_hide_a_large_orientation_jump():
    from execute.control import pose_precheck

    rotations = [Rotation.from_euler("z", angle).as_matrix() for angle in (0.01, 3.13)]
    waypoints = np.array([np.r_[np.zeros(3), r[:, 0], r[:, 1], 0] for r in rotations])
    assert pose_precheck(waypoints) is not None


def test_stock_pose_tool_rotates_and_closes_in_real_physics(tmp_path):
    import httpx
    from inspect_robots import Task, eval, success_at_end
    from inspect_robots_agent import LLMAgentPolicy

    from execute.control import pose_precheck

    body = LiberoEmbodiment(tmp_path, max_steps=100)
    requests = 0
    target = None

    def respond(request):
        nonlocal requests, target
        requests += 1
        if requests == 1:
            _, rotation = body.robot.pose()
            target = Rotation.from_rotvec([0.25, 0, 0]).as_matrix() @ rotation
            values = np.r_[target[:, 0], target[:, 1], 1]
            arguments = {
                "targets": dict(zip(("xx", "xy", "xz", "yx", "yy", "yz", "grip"), values)),
                "note": "test pose and close",
            }
            name = "move_to"
        else:
            name, arguments = "done", {"summary": "control test", "hindsight": "none"}
        return httpx.Response(
            200,
            json={
                "id": str(requests),
                "type": "message",
                "role": "assistant",
                "model": "test",
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "content": [
                    {"type": "tool_use", "id": f"call-{requests}", "name": name, "input": arguments}
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    policy = LLMAgentPolicy(
        model="claude-opus-5",
        wire="messages",
        max_llm_calls=2,
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "fixture"},
        pre_check=pose_precheck,
        transport=httpx.MockTransport(respond),
    )
    task = Task(
        name="pose-test",
        scenes=[Scene(id="test", instruction="control check")],
        scorer=success_at_end(),
        max_steps=100,
    )
    try:
        (log,) = eval(task, policy, body, log_dir=str(tmp_path / "inspect"))
        assert requests == 2
        assert Rotation.from_matrix(target @ body.robot.pose()[1].T).magnitude() < 0.03
        assert max(body.robot.observe()["gripper_qpos"]) < 0.01
        assert 15 <= body.robot.steps <= 100
        assert log.results.metrics["success_at_end"] == 0
        records = [
            json.loads(line)
            for line in (body.robot.output / "control.jsonl").read_text().splitlines()
        ]
        assert records[-1]["inspect_chunk_final"]
    finally:
        body.close()
