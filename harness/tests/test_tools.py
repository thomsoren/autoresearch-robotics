"""Tool-layer tests. No LIBERO, no MuJoCo: a fake robot with the same four methods."""

import asyncio
import base64
import json

import jsonschema
import pytest

from harness.tools import (
    ALLOWED_TOOLS,
    GRIPPER_SCHEMA,
    MOVE_TO_SCHEMA,
    STEP_SCHEMA,
    TOOL_NAMES,
    build_robot_tools,
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


def tools_by_name(robot):
    return {item.name: item for item in build_robot_tools(robot)}


def call(robot, name, args):
    return asyncio.run(tools_by_name(robot)[name].handler(args))


def test_allowed_tool_names_are_namespaced(tmp_path):
    assert ALLOWED_TOOLS == [f"mcp__robot__{name}" for name in TOOL_NAMES]
    assert set(TOOL_NAMES) == set(tools_by_name(FakeRobot(tmp_path)))


@pytest.mark.parametrize(
    ("schema", "minimal"),
    [
        (MOVE_TO_SCHEMA, {"xyz": [0.0, 0.0, 1.0]}),
        (GRIPPER_SCHEMA, {"closed": True}),
        (STEP_SCHEMA, {"action": [0.0] * 7}),
    ],
)
def test_budget_arguments_are_optional_in_the_wire_schema(schema, minimal):
    # The SDK validates arguments against these before the handler runs, and its
    # dict shorthand would have marked every parameter required.
    jsonschema.validate(instance=minimal, schema=schema)


def test_schema_rejects_out_of_range_action():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"action": [9.0] * 7}, schema=STEP_SCHEMA)


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
    result = call(FakeRobot(tmp_path), name, args)

    assert result.get("is_error") is not True
    images = [block for block in result["content"] if block["type"] == "image"]
    assert len(images) == 2
    assert all(block["mimeType"] == "image/png" for block in images)
    assert all(base64.standard_b64decode(block["data"]) == PNG for block in images)

    facts = json.loads(result["content"][0]["text"])
    assert facts["remaining_steps"] == 497
    assert facts["success"] is False


def test_text_block_carries_no_filesystem_paths(tmp_path):
    result = call(FakeRobot(tmp_path), "observe", {})
    assert "images" not in json.loads(result["content"][0]["text"])
    assert str(tmp_path) not in result["content"][0]["text"]


def test_optional_arguments_fall_back_to_robot_defaults(tmp_path):
    robot = FakeRobot(tmp_path)
    call(robot, "move_to", {"xyz": [0.0, 0.0, 1.0]})
    call(robot, "gripper", {"closed": False})
    call(robot, "step", {"action": [0.0] * 7})

    assert robot.calls == [
        ("move_to", {"xyz": [0.0, 0.0, 1.0], "max_steps": 60}),
        ("gripper", {"closed": False, "steps": 15}),
        ("step", {"action": [0.0] * 7, "repeat": 1}),
    ]


def test_explicit_arguments_are_passed_through(tmp_path):
    robot = FakeRobot(tmp_path)
    call(robot, "move_to", {"xyz": [0.1, 0.2, 0.9], "max_steps": 20})
    call(robot, "step", {"action": [0.0] * 7, "repeat": 5})

    assert robot.calls == [
        ("move_to", {"xyz": [0.1, 0.2, 0.9], "max_steps": 20}),
        ("step", {"action": [0.0] * 7, "repeat": 5}),
    ]


def test_robot_errors_become_readable_tool_errors(tmp_path):
    robot = FakeRobot(tmp_path, raises=ValueError("All action values must be in [-1, 1]"))
    result = call(robot, "step", {"action": [9.0] * 7})

    assert result["is_error"] is True
    assert "ValueError" in result["content"][0]["text"]
    assert "must be in [-1, 1]" in result["content"][0]["text"]
