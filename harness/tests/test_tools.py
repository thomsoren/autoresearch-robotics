"""Tool dispatch tests. No LIBERO, no MuJoCo: a fake robot with the same four methods."""

import base64
import json

import jsonschema
import pytest

from harness.tools import (
    GRIPPER_SCHEMA,
    MOVE_TO_SCHEMA,
    STEP_SCHEMA,
    TOOL_DEFS,
    TOOL_NAMES,
    dispatch,
)

PNG = base64.standard_b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeRobot:
    """Mirrors the parts of `simulation.sim.Robot` the tools touch."""

    def __init__(self, tmp_path, raises=None):
        self.tmp_path = tmp_path
        self.raises = raises
        self.calls = []
        self.done = False
        for camera in ("agentview", "robot0_eye_in_hand"):
            (tmp_path / f"{camera}.png").write_bytes(PNG)

    def _payload(self, **extra):
        return dict(
            instruction="open the middle drawer of the cabinet",
            images={
                camera: str(self.tmp_path / f"{camera}.png")
                for camera in ("agentview", "robot0_eye_in_hand")
            },
            eef_pos=[0.0, 0.0, 1.0],
            eef_quat_xyzw=[0.0, 0.0, 0.0, 1.0],
            gripper_qpos=[0.04, -0.04],
            steps=3,
            remaining_steps=497,
            success=False,
            done=False,
            **extra,
        )

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.raises is not None:
            raise self.raises

    def observe(self):
        self._record("observe")
        return self._payload()

    def move_to(self, xyz, max_steps=60, tolerance=0.01):
        self._record("move_to", xyz=xyz, max_steps=max_steps)
        return self._payload(reached=True, position_error=0.004)

    def gripper(self, closed, steps=15):
        self._record("gripper", closed=closed, steps=steps)
        return self._payload()

    def step(self, action, repeat=1):
        self._record("step", action=action, repeat=repeat)
        return self._payload()


def test_tool_definitions_cover_every_tool():
    assert [tool["name"] for tool in TOOL_DEFS] == list(TOOL_NAMES)
    assert all(tool["description"] and tool["input_schema"] for tool in TOOL_DEFS)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("observe", {}),
        ("move_to", {"xyz": [0.0, 0.0, 1.1]}),
        ("gripper", {"closed": True}),
        ("step", {"action": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0]}),
    ],
)
def test_every_tool_returns_facts_plus_both_camera_images(tmp_path, name, args):
    content, is_error = dispatch(FakeRobot(tmp_path), name, args)

    assert is_error is False
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 2
    assert all(block["source"]["media_type"] == "image/png" for block in images)
    assert all(base64.standard_b64decode(block["source"]["data"]) == PNG for block in images)

    facts = json.loads(content[0]["text"])
    assert facts["remaining_steps"] == 497
    assert facts["success"] is False


def test_text_block_carries_no_filesystem_paths(tmp_path):
    content, _ = dispatch(FakeRobot(tmp_path), "observe", {})
    assert "images" not in json.loads(content[0]["text"])
    assert str(tmp_path) not in content[0]["text"]


def test_optional_arguments_fall_back_to_robot_defaults(tmp_path):
    robot = FakeRobot(tmp_path)
    dispatch(robot, "move_to", {"xyz": [0.0, 0.0, 1.0]})
    dispatch(robot, "gripper", {"closed": False})
    dispatch(robot, "step", {"action": [0.0] * 7})

    assert robot.calls == [
        ("move_to", {"xyz": [0.0, 0.0, 1.0], "max_steps": 60}),
        ("gripper", {"closed": False, "steps": 15}),
        ("step", {"action": [0.0] * 7, "repeat": 1}),
    ]


def test_explicit_arguments_are_passed_through(tmp_path):
    robot = FakeRobot(tmp_path)
    dispatch(robot, "move_to", {"xyz": [0.1, 0.2, 0.9], "max_steps": 20})
    dispatch(robot, "step", {"action": [0.0] * 7, "repeat": 5})

    assert robot.calls == [
        ("move_to", {"xyz": [0.1, 0.2, 0.9], "max_steps": 20}),
        ("step", {"action": [0.0] * 7, "repeat": 5}),
    ]


def test_robot_errors_become_readable_tool_errors(tmp_path):
    robot = FakeRobot(tmp_path, raises=ValueError("All action values must be in [-1, 1]"))
    content, is_error = dispatch(robot, "step", {"action": [9.0] * 7})

    assert is_error is True
    assert "ValueError" in content[0]["text"]
    assert "must be in [-1, 1]" in content[0]["text"]


def test_unknown_tool_is_an_error_not_a_crash(tmp_path):
    content, is_error = dispatch(FakeRobot(tmp_path), "teleport", {})
    assert is_error is True
    assert "teleport" in content[0]["text"]


@pytest.mark.parametrize(
    ("schema", "minimal"),
    [
        (MOVE_TO_SCHEMA, {"xyz": [0.0, 0.0, 1.0]}),
        (GRIPPER_SCHEMA, {"closed": True}),
        (STEP_SCHEMA, {"action": [0.0] * 7}),
    ],
)
def test_budget_arguments_are_optional_in_the_schema(schema, minimal):
    jsonschema.validate(instance=minimal, schema=schema)


def test_schema_rejects_out_of_range_action():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"action": [9.0] * 7}, schema=STEP_SCHEMA)


def test_tool_results_carry_the_gripper_body_block(tmp_path):
    content, _ = dispatch(FakeRobot(tmp_path), "observe", {})
    body = json.loads(content[0]["text"])["body"]

    assert body["jaw_axis"] == [1.0, 0.0, 0.0]
    assert body["jaw_opening_m"] == 0.08
    assert body["jaw_max_opening_m"] == 0.08


def test_body_block_is_omitted_when_the_robot_reports_no_pose(tmp_path):
    robot = FakeRobot(tmp_path)
    original = robot.observe

    def poseless():
        payload = original()
        del payload["eef_quat_xyzw"]
        return payload

    robot.observe = poseless
    content, _ = dispatch(robot, "observe", {})
    assert "body" not in json.loads(content[0]["text"])


def test_timing_dict_splits_simulation_from_image_encoding(tmp_path):
    timing = {}
    dispatch(FakeRobot(tmp_path), "observe", {}, timing)

    assert timing["sim_seconds"] >= 0
    assert timing["encode_seconds"] >= 0
    assert timing["seconds"] == pytest.approx(
        timing["sim_seconds"] + timing["encode_seconds"], abs=1e-6
    )


def test_timing_dict_is_filled_in_even_when_the_robot_fails(tmp_path):
    robot = FakeRobot(tmp_path, raises=RuntimeError("boom"))
    timing = {}
    _content, is_error = dispatch(robot, "observe", {}, timing)

    assert is_error is True
    assert timing["seconds"] >= 0
    assert timing["encode_seconds"] == pytest.approx(0, abs=1e-3)
