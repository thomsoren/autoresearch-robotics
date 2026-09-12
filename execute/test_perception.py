"""Calibrated visible-pixel lookup with no simulator or API dependencies."""

import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from inspect_robots import Observation, Scene

from execute.inspect_agent import LiberoEmbodiment
from execute.operational_policy import OperationalPolicy
from execute.perception import locate_pixels


@pytest.fixture
def observation(tmp_path):
    depth = np.full((3, 4), 2.0, dtype=np.float32)
    depth[0, 0] = np.nan
    depth[0, 1] = 0
    depth[0, 2] = np.inf
    path = tmp_path / "depth.npz"
    np.savez_compressed(path, depth=depth)
    transform = [[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30], [0, 0, 0, 1]]
    return Observation(
        images={"camera": np.zeros((3, 4, 3), dtype=np.uint8)},
        state={
            "physics_steps": np.array([7]),
            "eef_pose": np.array([0, 0, 1, 1, 0, 0, 0, 1, 0, 0]),
        },
        extra={
            "camera_geometry": {
                "camera": {
                    "depth_path": str(path),
                    "intrinsics": [[2, 0, 1], [0, 2, 1], [0, 0, 1]],
                    "camera_to_world": transform,
                    "step": 7,
                }
            }
        },
    )


def test_pixel_column_row_and_live_transform(observation):
    result = locate_pixels(observation, "camera", [{"u": 3, "v": 2}, {"u": 1, "v": 1}])
    assert result["image_dimensions"] == {"width": 4, "height": 3}
    assert result["observation_step"] == 7
    np.testing.assert_allclose(result["points"][0]["world_xyz"], [9, 22, 32])
    np.testing.assert_allclose(result["points"][1]["world_xyz"], [10, 20, 32])
    assert result["points"][0]["pixel"] == {"u": 3, "v": 2}
    assert result["points"][0]["depth_m"] == 2
    assert "surface" in result["meaning"]


def test_invalid_samples_are_flagged_individually(observation):
    pixels = [
        {"u": 0, "v": 0},
        {"u": 1, "v": 0},
        {"u": 2, "v": 0},
        {"u": -1, "v": 1},
        {"u": 4, "v": 1},
        {"u": float("nan"), "v": 0},
        {"u": 1.2, "v": 1},
        {"u": 3, "v": 2},
    ]
    result = locate_pixels(observation, "camera", pixels)
    assert [p["valid"] for p in result["points"]] == [False] * 7 + [True]
    assert all("world_xyz" not in p for p in result["points"][:7])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("pixels", [[], [{"u": 1, "v": 1}] * 9, "bad"])
def test_query_count_is_bounded(observation, pixels):
    with pytest.raises(ValueError, match="1 to 8"):
        locate_pixels(observation, "camera", pixels)


def test_camera_and_snapshot_validation(observation):
    with pytest.raises(ValueError, match="camera"):
        locate_pixels(observation, "other", [{"u": 1, "v": 1}])
    geometry = observation.extra["camera_geometry"]["camera"]
    geometry["step"] = 6
    with pytest.raises(ValueError, match="stale"):
        locate_pixels(observation, "camera", [{"u": 1, "v": 1}])
    geometry["step"] = 7
    geometry["intrinsics"] = np.zeros((3, 3)).tolist()
    with pytest.raises(ValueError, match="intrinsics"):
        locate_pixels(observation, "camera", [{"u": 1, "v": 1}])


def test_lookup_tool_preserves_stock_actions_and_static_schema(tmp_path, observation):
    changed = OperationalPolicy(
        model="claude-fable-5-1",
        wire="messages",
        effort="low",
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "test-only"},
        wire_capture=False,
    )
    changed.bind(LiberoEmbodiment(tmp_path).info)
    schemas = changed._toolset.schemas()
    call = SimpleNamespace(
        name="locate_pixels",
        arguments=json.dumps({"camera": "camera", "pixels": [{"u": 3, "v": 2}]}),
    )
    result = changed._toolset.execute(call, observation)
    assert result.chunk is None and result.error is None
    assert json.loads(result.note)["points"][0]["valid"]
    assert changed._toolset.schemas() == schemas
    missing = changed._toolset.execute(call, Observation())
    assert missing.chunk is None and missing.error is not None
    assert changed._toolset.schemas() == schemas


def test_stock_policy_continues_after_sensor_query_without_motion(tmp_path, observation):
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) == 1:
            name, inputs = "locate_pixels", {"camera": "camera", "pixels": [{"u": 3, "v": 2}]}
        else:
            assert "world_xyz" in json.dumps(payload["messages"])
            assert payload["tools"] == sent[0]["tools"]
            name, inputs = "give_up", {"reason": "query verified", "hindsight": "none"}
        return httpx.Response(
            200,
            json={
                "id": str(len(sent)),
                "type": "message",
                "role": "assistant",
                "model": "claude-fable-5-1",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": str(len(sent)), "name": name, "input": inputs}
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    changed = OperationalPolicy(
        model="claude-fable-5-1",
        wire="messages",
        effort="low",
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        max_llm_calls=2,
        image_horizon=None,
        max_output_tokens=8192,
        env={"CLAUDE_API_KEY": "test-only"},
        wire_capture=False,
        transport=httpx.MockTransport(respond),
    )
    changed.bind(LiberoEmbodiment(tmp_path).info)
    changed.reset(Scene(id="test", instruction="inspect the visible surface"))
    chunk = changed.act(observation)
    assert len(sent) == 2
    assert len(chunk.actions) == 1 and chunk.actions[0].meta["request_stop"]


def test_missing_depth_and_mismatched_dimensions_are_rejected(observation, tmp_path):
    geometry = observation.extra["camera_geometry"]["camera"]
    geometry["depth_path"] = str(tmp_path / "missing.npz")
    with pytest.raises(ValueError, match="unavailable"):
        locate_pixels(observation, "camera", [{"u": 1, "v": 1}])
    path = tmp_path / "wrong-size.npz"
    np.savez_compressed(path, depth=np.ones((1, 1), dtype=np.float32))
    geometry["depth_path"] = str(path)
    with pytest.raises(ValueError, match="dimensions"):
        locate_pixels(observation, "camera", [{"u": 1, "v": 1}])
