"""Operational wording changes; all policy/physics behavior stays delegated."""

import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from inspect_robots import Observation, Scene
from inspect_robots_agent import LLMAgentPolicy

from execute.inspect_agent import LiberoEmbodiment
from execute.operational_policy import OperationalPolicy


def policy(cls, **kwargs):
    return cls(
        model="claude-fable-5-1",
        wire="messages",
        effort="low",
        image_horizon=None,
        max_output_tokens=8192,
        max_llm_calls=2,
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "test-only"},
        wire_capture=False,
        **kwargs,
    )


@pytest.mark.parametrize("images", ["always", "on_demand"])
def test_prompt_preserves_docs_skill_and_required_fields(tmp_path, images):
    skill = tmp_path / "SKILL.md"
    skill.write_text("Frozen advice: observe before moving.\n")
    body = LiberoEmbodiment(tmp_path / "unused", control="pose")
    stock = policy(LLMAgentPolicy, images=images, prior_learnings=str(skill))
    changed = policy(OperationalPolicy, images=images, prior_learnings=str(skill))
    for agent in (stock, changed):
        agent.bind(body.info)
        agent.reset(Scene(id="test", instruction="move an object"))
    original = stock.transcript()[0]["content"]
    updated = changed.transcript()[0]["content"]
    marker = "\n\nEmbodiment notes:\n"
    assert original.split(marker, 1)[1] == updated.split(marker, 1)[1]
    assert "why you chose" not in updated
    assert "wish you had" not in updated
    assert "2 LLM calls" in updated
    assert skill.read_text() in updated
    old = stock._toolset.schemas()
    new = changed._toolset.schemas()
    stock_names = {s["function"]["name"] for s in old}
    same_tools = [s for s in new if s["function"]["name"] in stock_names]
    assert new[-1]["function"]["name"] == "locate_pixels"
    for a, b in zip(old, same_tools, strict=True):
        assert a["function"]["name"] == b["function"]["name"]
        assert a["function"]["parameters"]["required"] == b["function"]["parameters"]["required"]
    serialized = json.dumps(new)
    assert "why you chose" not in serialized
    assert "wish you had" not in serialized
    assert "radians for rotations" not in serialized
    assert "unitless rot6d" in serialized


def test_valid_actions_and_missing_note_error(tmp_path):
    body = LiberoEmbodiment(tmp_path, control="pose")
    agents = [policy(cls) for cls in (LLMAgentPolicy, OperationalPolicy)]
    for agent in agents:
        agent.bind(body.info)
    obs = Observation(state={"eef_pose": np.array([0, 0, 1, 1, 0, 0, 0, 1, 0, 0])})
    call = SimpleNamespace(
        name="move_to",
        arguments=json.dumps({"targets": {"z": 1.02}, "note": "Object visible; moving upward."}),
    )
    old, new = [a._toolset.execute(call, obs) for a in agents]
    assert old.note == new.note and old.error == new.error
    assert len(old.chunk.actions) == len(new.chunk.actions)
    for a, b in zip(old.chunk.actions, new.chunk.actions, strict=True):
        np.testing.assert_array_equal(a.data, b.data)
        assert a.meta == b.meta
    call.arguments = json.dumps({"targets": {"z": 1.02}})
    rejected = agents[1]._toolset.execute(call, obs)
    assert rejected.chunk is None
    assert "note is required" in rejected.error
    assert "why you chose" not in rejected.error


def test_xyz_description_is_not_replaced_with_rot6d(tmp_path):
    changed = policy(OperationalPolicy)
    changed.bind(LiberoEmbodiment(tmp_path, control="xyz").info)
    description = changed._toolset.schemas()[0]["function"]["description"]
    assert "Move BY" in description
    assert "rot6d" not in description


def test_stock_template_drift_fails_closed(tmp_path, monkeypatch):
    import inspect_robots_agent.policy as upstream

    monkeypatch.setattr(upstream, "_SYSTEM_TEMPLATE", upstream._SYSTEM_TEMPLATE + " Changed.")
    changed = policy(OperationalPolicy)
    changed.bind(LiberoEmbodiment(tmp_path).info)
    with pytest.raises(RuntimeError, match="template changed"):
        changed.reset(Scene(id="test", instruction="move"))


def test_refusal_is_not_converted_to_a_tool_call(tmp_path):
    def respond(request):
        body = json.loads(request.content)
        assert "why you chose" not in json.dumps(body["tools"])
        return httpx.Response(
            200,
            json={
                "stop_reason": "refusal",
                "stop_details": {"category": "reasoning_extraction"},
                "content": [{"type": "text", "text": "Public refusal"}],
            },
        )

    changed = policy(OperationalPolicy, transport=httpx.MockTransport(respond))
    changed.bind(LiberoEmbodiment(tmp_path).info)
    changed.reset(Scene(id="test", instruction="move"))
    with pytest.raises(RuntimeError, match="LLM refused"):
        changed.act(Observation(state={"eef_pose": np.array([0, 0, 1, 1, 0, 0, 0, 1, 0, 0])}))
