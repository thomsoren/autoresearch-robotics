"""Reactive action-chunk playback through Inspect's public controller contract."""

from collections import deque

import httpx
import numpy as np
from inspect_robots import Action, ActionChunk, Observation, Scene
from inspect_robots.mock import CubePickEmbodiment
from inspect_robots_agent import LLMAgentPolicy

from execute.reactive_controller import ReactiveController


class Policy:
    def __init__(self, chunks):
        self.chunks = deque(chunks)
        self.observations = []

    def act(self, observation):
        self.observations.append(observation)
        return self.chunks.popleft()


def chunk(*values):
    return ActionChunk(actions=[Action(np.array([value], dtype=float)) for value in values])


def observation(*, discard=False, marker=0):
    return Observation(
        state={"marker": np.array([marker], dtype=float)},
        extra={"discard_action_chunk": discard},
    )


def test_failed_motion_discards_stale_actions_and_replans_from_fresh_observation():
    controller = ReactiveController()
    policy = Policy([chunk(1, 2, 3), chunk(9)])
    store = {}

    first = controller.next_action(policy, observation(marker=1), 0, store)
    fresh = observation(discard=True, marker=2)
    replanned = controller.next_action(policy, fresh, 1, store)

    assert first.data.tolist() == [1]
    assert replanned.data.tolist() == [9]
    assert len(policy.observations) == 2
    assert policy.observations[1] is fresh


def test_successful_motion_keeps_chunk_and_controller_state_is_per_trial():
    controller = ReactiveController()
    policy = Policy([chunk(1, 2, 3), chunk(7)])
    first_trial = {}

    actions = [
        controller.next_action(policy, observation(marker=1), step, first_trial).data.item()
        for step in range(3)
    ]
    second_trial_action = controller.next_action(
        policy, observation(marker=8), 0, {}
    ).data.item()

    assert actions == [1, 2, 3]
    assert second_trial_action == 7
    assert [item.state["marker"].item() for item in policy.observations] == [1, 8]


def test_discard_replans_real_llm_policy_instead_of_replaying_stale_chunk():
    requests = 0

    def respond(request):
        nonlocal requests
        requests += 1
        deltas = {"dx": 0.1} if requests == 1 else {"dy": 0.02}
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
                    {
                        "type": "tool_use",
                        "id": f"call-{requests}",
                        "name": "move_by",
                        "input": {"deltas": deltas, "note": "mocked move"},
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    policy = LLMAgentPolicy(
        model="claude-opus-5",
        wire="messages",
        max_llm_calls=2,
        wire_capture=False,
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "fixture"},
        transport=httpx.MockTransport(respond),
    )
    body = CubePickEmbodiment()
    scene = Scene(id="test", instruction="move twice")
    policy.bind(body.info)
    policy.reset(scene)
    first_observation = body.reset(scene)
    fresh_observation = Observation(
        images=first_observation.images,
        state=first_observation.state,
        instruction=first_observation.instruction,
        extra={"discard_action_chunk": True, "env_step": 1},
    )
    controller = ReactiveController()
    store = {}

    try:
        first = controller.next_action(policy, first_observation, 0, store)
        replanned = controller.next_action(policy, fresh_observation, 1, store)
    finally:
        body.close()

    assert requests == 2
    assert first.data[0] > 0 and first.data[1] == 0
    assert replanned.data[0] == 0 and replanned.data[1] > 0
