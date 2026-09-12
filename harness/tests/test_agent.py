"""Agent-loop tests against a stub Anthropic client. No network, no LIBERO."""

import anthropic
import httpx2
import pytest

from harness.agent import FAST_MODE_BETA, RobotAgent
from harness.tests.test_tools import FakeRobot


class Block:
    def __init__(self, type, **fields):
        self.type = type
        self.__dict__.update(fields)


class Response:
    def __init__(self, content, stop_reason, speed=None):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = Block("usage", input_tokens=10, output_tokens=5, speed=speed)


class StubMessages:
    """Replays a scripted list of responses and records every request."""

    def __init__(self, responses, fail_with=None):
        self.responses = list(responses)
        self.fail_with = fail_with
        self.requests = []

    def create(self, **params):
        # Snapshot: the agent keeps mutating the same messages list afterwards.
        self.requests.append({**params, "messages": list(params["messages"])})
        if self.fail_with is not None and "speed" in params:
            raise self.fail_with
        return self.responses.pop(0)


class StubClient:
    def __init__(self, responses, fail_with=None):
        self.messages = StubMessages(responses, fail_with)
        self.beta = Block("beta", messages=self.messages)


def text_response(text):
    return Response([Block("text", text=text)], "end_turn")


def tool_response(name, args, tool_id="t1"):
    return Response([Block("tool_use", id=tool_id, name=name, input=args)], "tool_use")


def agent_for(tmp_path, responses, fail_with=None, **kwargs):
    client = StubClient(responses, fail_with)
    robot = FakeRobot(tmp_path)
    return RobotAgent(robot, "system", client=client, **kwargs), client, robot


def test_tool_call_is_dispatched_and_result_fed_back(tmp_path):
    agent, client, robot = agent_for(
        tmp_path,
        [tool_response("move_to", {"xyz": [0.0, 0.0, 1.0]}), text_response("done")],
    )

    events = list(agent.send("move up"))

    assert [kind for kind, _ in events] == [
        "api",
        "tool_use",
        "tool_result",
        "api",
        "text",
        "stop",
    ]
    assert robot.calls == [("move_to", {"xyz": [0.0, 0.0, 1.0], "max_steps": 60})]
    # Second request carries the tool_result as a single user message.
    results = client.messages.requests[1]["messages"][-1]
    assert results["role"] == "user"
    assert results["content"][0]["type"] == "tool_result"
    assert results["content"][0]["tool_use_id"] == "t1"


def test_parallel_tool_calls_return_in_one_user_message(tmp_path):
    both = Response(
        [
            Block("tool_use", id="a", name="observe", input={}),
            Block("tool_use", id="b", name="gripper", input={"closed": True}),
        ],
        "tool_use",
    )
    agent, client, _ = agent_for(tmp_path, [both, text_response("ok")])

    list(agent.send("look and grip"))

    results = client.messages.requests[1]["messages"][-1]["content"]
    assert [block["tool_use_id"] for block in results] == ["a", "b"]


def test_fast_mode_sets_speed_and_beta(tmp_path):
    agent, client, _ = agent_for(tmp_path, [text_response("hi")], fast=True, model="claude-opus-5")

    list(agent.send("hello"))

    assert agent.fast is True
    assert client.messages.requests[0]["speed"] == "fast"
    assert client.messages.requests[0]["betas"] == [FAST_MODE_BETA]


def test_fast_mode_refused_on_unsupported_model(tmp_path):
    agent, client, _ = agent_for(
        tmp_path, [text_response("hi")], fast=True, model="claude-sonnet-5"
    )

    list(agent.send("hello"))

    assert agent.fast is False
    assert "only available on" in agent.fast_disabled_reason
    assert "speed" not in client.messages.requests[0]


def test_fast_mode_rate_limit_falls_back_to_standard(tmp_path):
    response = httpx2.Response(
        429, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    limit = anthropic.RateLimitError("slow down", response=response, body=None)
    agent, client, _ = agent_for(
        tmp_path, [text_response("hi")], fail_with=limit, fast=True, model="claude-opus-5"
    )

    list(agent.send("hello"))

    assert agent.fast is False
    assert "rate limit" in agent.fast_disabled_reason
    assert "speed" not in client.messages.requests[-1]


def test_refusal_stops_before_reading_content(tmp_path):
    refusal = Response([], "refusal")
    agent, _, _ = agent_for(tmp_path, [refusal])

    assert [kind for kind, _ in agent.send("hi")] == ["api", "stop"]
    assert agent.timing["api_calls"] == 1


def test_episode_done_ends_the_turn(tmp_path):
    agent, client, robot = agent_for(tmp_path, [tool_response("observe", {})])
    robot.done = True

    events = list(agent.send("look"))

    assert events[-1] == ("stop", "episode_done")
    assert len(client.messages.requests) == 1


def test_max_turns_is_enforced(tmp_path):
    responses = [tool_response("observe", {}, tool_id=f"t{i}") for i in range(5)]
    agent, client, _ = agent_for(tmp_path, responses, max_turns=3)

    assert list(agent.send("loop"))[-1] == ("stop", "max_turns")
    assert len(client.messages.requests) == 3


def test_usage_accumulates_across_requests(tmp_path):
    agent, _, _ = agent_for(tmp_path, [tool_response("observe", {}), text_response("done")])

    list(agent.send("look"))

    assert agent.usage == {"input_tokens": 20, "output_tokens": 10, "requests": 2}


def test_effort_is_sent_only_when_set(tmp_path):
    agent, client, _ = agent_for(tmp_path, [text_response("hi")], effort="low")
    list(agent.send("hello"))
    assert client.messages.requests[0]["output_config"] == {"effort": "low"}

    plain, plain_client, _ = agent_for(tmp_path, [text_response("hi")])
    list(plain.send("hello"))
    assert "output_config" not in plain_client.messages.requests[0]


def test_system_prompt_is_cached(tmp_path):
    agent, client, _ = agent_for(tmp_path, [text_response("hi")])
    list(agent.send("hello"))
    system = client.messages.requests[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize("done", [False, True])
def test_robot_done_gate_uses_live_state(tmp_path, done):
    agent, _, robot = agent_for(tmp_path, [tool_response("observe", {}), text_response("x")])
    robot.done = done
    last = list(agent.send("look"))[-1]
    assert last == (("stop", "episode_done") if done else ("stop", "end_turn"))


def test_timing_splits_model_time_from_tool_time(tmp_path):
    agent, _, _ = agent_for(tmp_path, [tool_response("observe", {}), text_response("done")])

    events = dict((kind, payload) for kind, payload in agent.send("look"))
    timing = agent.timing

    assert timing["api_calls"] == 2
    assert timing["tool_calls"] == 1
    assert timing["per_tool"]["observe"]["calls"] == 1
    # Every slice is real time, and the parts never exceed the whole.
    assert events["api"] >= 0
    assert timing["api_seconds"] > 0
    assert timing["tool_seconds"] > 0
    assert timing["sim_seconds"] + timing["encode_seconds"] <= timing["tool_seconds"] + 1e-6
    assert timing["api_seconds"] + timing["tool_seconds"] <= timing["wall_seconds"] + 1e-6


def test_tool_result_event_carries_its_own_duration(tmp_path):
    agent, _, _ = agent_for(tmp_path, [tool_response("observe", {}), text_response("done")])

    results = [payload for kind, payload in agent.send("look") if kind == "tool_result"]

    assert len(results) == 1
    name, _summary, is_error, seconds = results[0]
    assert (name, is_error) == ("observe", False)
    assert seconds == pytest.approx(agent.timing["tool_seconds"])


def test_wall_time_accumulates_across_instructions(tmp_path):
    agent, _, _ = agent_for(tmp_path, [text_response("a"), text_response("b")])

    list(agent.send("one"))
    first = agent.timing["wall_seconds"]
    list(agent.send("two"))

    assert agent.timing["wall_seconds"] > first
