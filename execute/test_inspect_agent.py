"""Experimental adapter and stock-agent contract checks; no live API calls."""

import json
import time

import numpy as np
import pytest

pytest.importorskip("inspect_robots_agent", reason="Install the execute dependency group")
import httpx  # noqa: E402
from inspect_robots import Action, Observation, Scene, Task, eval, success_at_end  # noqa: E402
from inspect_robots.mock import CubePickEmbodiment  # noqa: E402
from inspect_robots_agent import LLMAgentPolicy  # noqa: E402

from execute.inspect_agent import LiberoEmbodiment, RequestBudget  # noqa: E402


class FakeRobot:
    gripper_command = -1.0

    def __init__(self):
        self.calls = []

    def step(self, action):
        self.calls.append(action)
        self.gripper_command = action[-1]
        return {"success": False, "done": False}


def test_physical_displacements_gripper_hold_and_fixed_rotation(tmp_path):
    body = LiberoEmbodiment(tmp_path)
    body.robot = FakeRobot()
    body.observation = lambda raw: Observation()
    result = body.step(Action(np.array([0.01, -0.01, 0.005, 0.2])))
    np.testing.assert_allclose(body.robot.calls[-1], [0.2, -0.2, 0.1, 0, 0, 0, 1])
    body.step(Action(np.zeros(4)))
    assert body.robot.calls[-1][-1] == 1  # Omitted grip retains closed command.
    body.step(Action(np.array([0, 0, 0, -0.1])))
    assert body.robot.calls[-1][-1] == -1
    assert result.reward == 0 and not result.terminated


@pytest.mark.parametrize("data", [[1, 0, 0, 0], [float("nan"), 0, 0, 0], [0, 0, 0]])
def test_invalid_action_never_reaches_simulation(tmp_path, data):
    body = LiberoEmbodiment(tmp_path)
    body.robot = FakeRobot()
    with pytest.raises(ValueError):
        body.step(Action(np.array(data)))
    assert body.robot.calls == []


def test_stock_opus_fast_agent_done_cannot_declare_success(tmp_path):
    def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        body = json.loads(request.content)
        assert body["model"] == "claude-opus-5"
        assert body["speed"] == "fast"
        assert "anthropic-beta" in request.headers
        return httpx.Response(
            200,
            json={
                "id": "test",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_test",
                        "name": "done",
                        "input": {"summary": "I claim success", "hindsight": "none"},
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    policy = LLMAgentPolicy(
        model="claude-opus-5",
        wire="messages",
        speed="fast",
        max_output_tokens=1024,
        max_llm_calls=1,
        effort="low",
        wire_capture=False,
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "test-only-key"},
        transport=httpx.MockTransport(respond),
    )
    body = CubePickEmbodiment()
    task = Task(
        name="test-done",
        scenes=[Scene(id="one", instruction="reach cube")],
        scorer=success_at_end(),
        max_steps=2,
    )
    try:
        (log,) = eval(task, policy, body, log_dir=str(tmp_path))
    finally:
        body.close()
    assert log.status == "success", log.samples
    assert log.results.metrics["success_at_end"] == 0
    assert log.samples[0].policy_transcripts[0]


def test_request_budget_covers_retries_and_logs_no_credentials(tmp_path):
    budget = RequestBudget(tmp_path, 1, time.monotonic() + 30)
    budget.inner.close()
    budget.inner = httpx.MockTransport(
        lambda req: httpx.Response(
            200, json={"model": "claude-opus-5", "usage": {"input_tokens": 1, "speed": "fast"}}
        )
    )
    with httpx.Client(transport=budget) as client:
        client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": "private-test-key"},
            json={"model": "claude-opus-5", "speed": "fast"},
        )
        with pytest.raises(RuntimeError, match="budget exhausted"):
            client.post("https://api.anthropic.com/v1/messages", json={})
    text = (tmp_path / "requests.jsonl").read_text()
    assert "private-test-key" not in text
    assert len(text.splitlines()) == 1
    assert json.loads(text)["usage"]["speed"] == "fast"


def test_fable_preserves_image_history_and_thinking_prefixes(tmp_path):
    from inspect_robots import Scene

    from execute.inspect_agent import image_horizon

    requests = []

    def without_cache_markers(value):
        if isinstance(value, dict):
            return {k: without_cache_markers(v) for k, v in value.items() if k != "cache_control"}
        if isinstance(value, list):
            return [without_cache_markers(v) for v in value]
        return value

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "claude-fable-5-1"
        assert body["thinking"] == {"type": "adaptive"}
        assert body.get("tool_choice", {}).get("type", "auto") == "auto"
        assert "speed" not in body
        current = without_cache_markers(body)
        if requests:
            previous = requests[-1]
            assert current["messages"][: len(previous["messages"])] == previous["messages"]
            assert current["system"] == previous["system"]
            assert current["tools"] == previous["tools"]
        requests.append(current)
        return httpx.Response(
            200,
            json={
                "id": f"message-{len(requests)}",
                "type": "message",
                "role": "assistant",
                "model": "claude-fable-5-1",
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "test",
                        "signature": f"fixture-{len(requests)}",
                    },
                    {
                        "type": "tool_use",
                        "id": f"call-{len(requests)}",
                        "name": "move_by",
                        "input": {"deltas": {"dy": -0.001}, "note": "bounded test"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    assert image_horizon("claude-opus-5") == 2
    assert image_horizon("claude-fable-5-1") is None
    policy = LLMAgentPolicy(
        model="claude-fable-5-1",
        wire="messages",
        speed=None,
        effort="low",
        max_output_tokens=1024,
        max_llm_calls=4,
        images="always",
        image_horizon=image_horizon("claude-fable-5-1"),
        wire_capture=False,
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "test-only"},
        transport=httpx.MockTransport(respond),
    )
    policy.bind(LiberoEmbodiment(tmp_path).info)
    policy.reset(Scene(id="test", instruction="open drawer"))
    for i in range(4):
        policy.act(
            Observation(
                images={"agentview": np.full((8, 8, 3), i, dtype=np.uint8)},
                state={"eef_pos": np.zeros(3)},
            )
        )
    assert len(requests) == 4
