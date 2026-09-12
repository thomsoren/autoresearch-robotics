"""Robot tool definitions and dispatch, in Anthropic Messages API shape.

The schemas are plain JSON Schema and the dispatcher is a plain function, so this
module has no SDK dependency of its own. `Robot` owns an OpenGL context and must be
driven serially from the thread that built it; the whole harness is synchronous for
that reason, so a dispatch call is always on the right thread.
"""

import base64
import json
import time
from pathlib import Path

from harness.geometry import body_state

TOOL_NAMES = ("observe", "move_to", "gripper", "step")

OBSERVE_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

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

TOOL_DEFS = [
    {
        "name": "observe",
        "description": (
            "Look at the scene. Returns the task instruction, both camera images (agentview "
            "external and robot0_eye_in_hand wrist), end-effector position and orientation, "
            "gripper opening, and the remaining control-step budget. Consumes no steps."
        ),
        "input_schema": OBSERVE_SCHEMA,
    },
    {
        "name": "move_to",
        "description": (
            "Move the end effector toward a world-coordinate XYZ target in meters, holding the "
            "current orientation. Feedback controller only: it does not plan around obstacles "
            "and may fail to reach. Always check `reached` and `position_error` in the result. "
            "Consumes up to `max_steps` control steps."
        ),
        "input_schema": MOVE_TO_SCHEMA,
    },
    {
        "name": "gripper",
        "description": (
            "Close or open the gripper, holding the command for a bounded number of control "
            "steps. Consumes `steps` control steps."
        ),
        "input_schema": GRIPPER_SCHEMA,
    },
    {
        "name": "step",
        "description": (
            "Send a raw normalized OSC delta action [dx, dy, dz, rx, ry, rz, grip], every value "
            "in [-1, 1]. One translation unit is 5 cm, one rotation unit is 0.5 rad, grip -1 "
            "opens and +1 closes. This is the only way to rotate the wrist. Each repeat consumes "
            "one control step."
        ),
        "input_schema": STEP_SCHEMA,
    },
]


def _content(payload):
    """Turn a `Robot` result dict into tool-result content blocks.

    The camera PNGs the robot already wrote become image blocks; everything else
    becomes one JSON text block with the file paths removed, so the model reads
    pixels rather than filenames.
    """
    images = payload.get("images") or {}
    facts = {key: value for key, value in payload.items() if key != "images"}
    body = body_state(payload)
    if body is not None:
        facts["body"] = body
    blocks = [{"type": "text", "text": json.dumps(facts, indent=2, sort_keys=True)}]
    for camera, path in images.items():
        blocks.append({"type": "text", "text": f"camera: {camera}"})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(Path(path).read_bytes()).decode(),
                },
            }
        )
    return blocks


def dispatch(robot, name, args, timing=None):
    """Run one tool call against `robot`.

    Returns `(content_blocks, is_error)`. A `Robot` failure is reported to the model
    as an error result rather than raised, so a bad call teaches it instead of ending
    the episode.

    Pass a dict as `timing` to have the two costs filled in separately: `sim_seconds`
    is physics plus rendering inside `Robot`, `encode_seconds` is reading the camera
    PNGs back off disk and base64-encoding them for the API.
    """
    started = time.perf_counter()
    try:
        if name == "observe":
            payload = robot.observe()
        elif name == "move_to":
            payload = robot.move_to(args["xyz"], max_steps=args.get("max_steps", 60))
        elif name == "gripper":
            payload = robot.gripper(args["closed"], steps=args.get("steps", 15))
        elif name == "step":
            payload = robot.step(args["action"], repeat=args.get("repeat", 1))
        else:
            _fill(timing, started, started)
            return [{"type": "text", "text": f"Unknown tool: {name}"}], True
    except Exception as exc:
        _fill(timing, started, time.perf_counter())
        return [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], True
    simulated = time.perf_counter()
    content = _content(payload)
    _fill(timing, started, simulated)
    return content, False


def _fill(timing, started, simulated):
    """Split the elapsed time into the simulation part and the encoding part."""
    if timing is None:
        return
    finished = time.perf_counter()
    timing["sim_seconds"] = simulated - started
    timing["encode_seconds"] = finished - simulated
    timing["seconds"] = finished - started
