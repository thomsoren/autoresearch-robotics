"""In-process SDK tools that expose one live `Robot` to a Claude Agent SDK session.

Every handler calls the robot directly on the event-loop thread. The robot owns an
OpenGL context and must be used serially from the thread that built it, so nothing
here may hop to a worker thread (no `asyncio.to_thread`, no executor).

Schemas are written as explicit JSON Schema rather than the decorator's dict shorthand,
because the shorthand marks every declared parameter as required and these tools have
genuinely optional budget arguments.
"""

import base64
import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

SERVER_NAME = "robot"
TOOL_NAMES = ("observe", "move_to", "gripper", "step")

ALLOWED_TOOLS = [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]

MOVE_TO_SCHEMA = {
    "type": "object",
    "properties": {
        "xyz": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
            "description": "Target [x, y, z] in world meters, e.g. [-0.05, 0.02, 1.0].",
        },
        "max_steps": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Control-step budget for this move (default 60).",
        },
    },
    "required": ["xyz"],
    "additionalProperties": False,
}

GRIPPER_SCHEMA = {
    "type": "object",
    "properties": {
        "closed": {
            "type": "boolean",
            "description": "True closes the gripper, False opens it.",
        },
        "steps": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Control steps to hold the command (default 15).",
        },
    },
    "required": ["closed"],
    "additionalProperties": False,
}

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "array",
            "items": {"type": "number", "minimum": -1, "maximum": 1},
            "minItems": 7,
            "maxItems": 7,
            "description": "[dx, dy, dz, rx, ry, rz, grip], each value in [-1, 1].",
        },
        "repeat": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Control steps to repeat the action for (default 1).",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}


def _content(payload):
    """Turn a `Robot` result dict into tool-result content blocks.

    The camera PNGs the robot already wrote become image blocks; everything else
    becomes one JSON text block with the file paths removed, so the model reads
    pixels rather than filenames.
    """
    images = payload.get("images") or {}
    facts = {key: value for key, value in payload.items() if key != "images"}
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(facts, indent=2, sort_keys=True)}
    ]
    for camera, path in images.items():
        blocks.append({"type": "text", "text": f"camera: {camera}"})
        blocks.append(
            {
                "type": "image",
                "data": base64.standard_b64encode(Path(path).read_bytes()).decode(),
                "mimeType": "image/png",
            }
        )
    return {"content": blocks}


def _error(exc):
    return {
        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
        "is_error": True,
    }


def build_robot_tools(robot):
    """Build the four SDK tools bound to `robot`."""

    @tool(
        "observe",
        "Look at the scene. Returns the task instruction, both camera images (agentview "
        "external and robot0_eye_in_hand wrist), end-effector position and orientation, "
        "gripper opening, and the remaining control-step budget. Consumes no steps.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def observe(args):
        try:
            return _content(robot.observe())
        except Exception as exc:  # report to the model; never kill the session
            return _error(exc)

    @tool(
        "move_to",
        "Move the end effector toward a world-coordinate XYZ target in meters, holding the "
        "current orientation. Feedback controller only: it does not plan around obstacles and "
        "may fail to reach. Always check `reached` and `position_error` in the result. Consumes "
        "up to `max_steps` control steps.",
        MOVE_TO_SCHEMA,
    )
    async def move_to(args):
        try:
            return _content(robot.move_to(args["xyz"], max_steps=args.get("max_steps", 60)))
        except Exception as exc:
            return _error(exc)

    @tool(
        "gripper",
        "Close or open the gripper, holding the command for a bounded number of control steps. "
        "Consumes `steps` control steps.",
        GRIPPER_SCHEMA,
    )
    async def gripper(args):
        try:
            return _content(robot.gripper(args["closed"], steps=args.get("steps", 15)))
        except Exception as exc:
            return _error(exc)

    @tool(
        "step",
        "Send a raw normalized OSC delta action [dx, dy, dz, rx, ry, rz, grip], every value in "
        "[-1, 1]. One translation unit is 5 cm, one rotation unit is 0.5 rad, grip -1 opens and "
        "+1 closes. This is the only way to rotate the wrist. Each repeat consumes one control step.",
        STEP_SCHEMA,
    )
    async def step(args):
        try:
            return _content(robot.step(args["action"], repeat=args.get("repeat", 1)))
        except Exception as exc:
            return _error(exc)

    return [observe, move_to, gripper, step]


def build_robot_server(robot):
    """Build an in-process MCP server exposing `robot` as four tools."""
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="0.1.0",
        tools=build_robot_tools(robot),
    )
