"""Open-loop playback that replans after measured motion failure.

This controller uses only Inspect Robots' public ``Controller.next_action``
contract.  Inspect does not expose a public controller-inference bookkeeping
hook, so policy transcripts and executor request logs remain the authoritative
record of model calls.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from inspect_robots import Action, Observation, Policy

_BUFFER_KEY = "autoresearch_reactive_controller_action_buffer"


class ReactiveController:
    """Play complete successful chunks and discard their tail after a failed motion."""

    def __init__(self, replan_interval: int | None = None):
        if replan_interval is not None and replan_interval < 1:
            raise ValueError("replan_interval must be >= 1 or None")
        self.replan_interval = replan_interval

    def next_action(
        self, policy: Policy, observation: Observation, t: int, store: dict[str, Any]
    ) -> Action:
        """Return the next buffered action, replanning when feedback rejects the tail."""
        del t
        buffer: deque[Action] = store.setdefault(_BUFFER_KEY, deque())
        if observation.extra.get("discard_action_chunk") is True:
            buffer.clear()

        if not buffer:
            chunk = policy.act(observation)
            take = self.replan_interval or len(chunk)
            buffer.extend(list(chunk.actions)[:take])
        return buffer.popleft()
