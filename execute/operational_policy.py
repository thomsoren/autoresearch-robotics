"""Pinned Inspect wording adapter; stock actions, validation and refusals are retained.

Only public observation/action summaries are requested. This adapter makes no
claim about the cause of provider refusals and never suppresses one.
"""

import copy
import hashlib
import json
from dataclasses import replace

import inspect_robots_agent.policy as upstream
from inspect_robots_agent import LLMAgentPolicy
from inspect_robots_agent._tools import ToolResult

from execute.perception import LOCATE_PIXELS_SCHEMA, locate_pixels

_TEMPLATE_HASHES = {
    "always": "efab10dc04ddc2be6915cf12b7960451a776a1fc4747347306ebb147ab5abd92",
    "on_demand": "2f2a073109d15f605f0400cd31e17683e382c196d491f7aef1112a50de4a85a8",
}
_NOTE = (
    "Briefly describe the visible scene or measured state and the requested action, "
    "in one or two plain sentences for the operator."
)
_OUTCOME = (
    "Summarize observable episode outcomes, such as measured motion or visible "
    "object changes. Say 'none' if there is no outcome to report."
)
_PREFIX = (
    "You control robot embodiment {name!r} through tool calls. Each observation "
    "contains measured robot state. {images} Work toward the user's goal in small "
    "motions and check the next observation after every motion. Every move tool "
    "call must include a note: briefly describe the visible scene or measured "
    "state and the requested action, in plain sentences for the operator. "
    "Safety approvers clamp out-of-bounds and too-fast actions below you. "
    "Operator feedback comes from the human supervising the robot. "
    "{turns} Use locate_pixels to measure selected visible surface pixels before "
    "choosing world-coordinate targets when calibrated depth is available. It "
    "does not move the robot and consumes a model call. When the goal appears "
    "achieved call done; the benchmark decides "
    "success. If it cannot be achieved call give_up. The summary, reason and "
    "hindsight fields describe observable outcomes; use 'none' for hindsight "
    "when there is no outcome to report. You have a budget of {budget} LLM calls "
    "for the whole trial."
)


class _OperationalToolset:
    """Delegate executable behavior, changing only schemas and missing-note wording."""

    def __init__(self, original, rotation_repr):
        self._original = original
        self._rotation_repr = rotation_repr

    def __getattr__(self, name):
        return getattr(self._original, name)

    def schemas(self):
        schemas = copy.deepcopy(self._original.schemas())
        for schema in schemas:
            function = schema["function"]
            properties = function["parameters"]["properties"]
            if "note" in properties:
                properties["note"]["description"] = _NOTE
            if "hindsight" in properties:
                properties["hindsight"]["description"] = _OUTCOME
            if function["name"] == "move_to" and self._rotation_repr == "rot6d":
                function["description"] = (
                    "Move to absolute end-effector targets. Positions are meters; "
                    "orientation uses unitless rot6d axes in the embodiment's "
                    "documented frame. Follow its axis convention. These are not "
                    "Euler angles or trial-relative radians. Unnamed dimensions "
                    "hold their observed values. Targets are interpolated into "
                    "bounded waypoints; each waypoint can consume multiple physics "
                    "steps. Check measured results rather than assuming arrival. "
                    + self._original._bounds_text
                )
        schemas.append(copy.deepcopy(LOCATE_PIXELS_SCHEMA))
        return schemas

    def execute(self, call, observation):
        if call.name == "locate_pixels":
            try:
                arguments = json.loads(call.arguments)
                if not isinstance(arguments, dict) or set(arguments) != {"camera", "pixels"}:
                    raise ValueError("locate_pixels requires only camera and pixels")
                result = locate_pixels(observation, arguments["camera"], arguments["pixels"])
                return ToolResult(note=json.dumps(result, allow_nan=False))
            except (TypeError, ValueError) as exc:
                return ToolResult(error=str(exc))
        result = self._original.execute(call, observation)
        if result.error in (
            "note is required: describe what you observe and why you chose this motion",
            "note is required: describe what you observe and why you chose this capture",
        ):
            return replace(result, error="note is required: summarize the observation and action")
        return result


class OperationalPolicy(LLMAgentPolicy):
    """Keep Inspect's policy lifecycle with narrowly adapted public-summary wording."""

    def bind(self, embodiment_info):
        super().bind(embodiment_info)
        semantics = embodiment_info.action_space.semantics
        self._toolset = _OperationalToolset(self._toolset, semantics.rotation_repr)

    def reset(self, scene):
        template = (
            upstream._ON_DEMAND_SYSTEM_TEMPLATE
            if self._images == "on_demand"
            else upstream._SYSTEM_TEMPLATE
        )
        if hashlib.sha256(template.encode()).hexdigest() != _TEMPLATE_HASHES[self._images]:
            raise RuntimeError("Inspect stock system template changed; review wording adapter")
        super().reset(scene)
        original_prefix = template.format(name=self._embodiment_name, budget=self._max_llm_calls)
        message = self._messages[0]
        if message["role"] != "system" or not message["content"].startswith(original_prefix):
            raise RuntimeError("Inspect system prefix changed; review wording adapter")
        on_demand = self._images == "on_demand"
        replacement = _PREFIX.format(
            name=self._embodiment_name,
            budget=self._max_llm_calls,
            images=(
                "Camera images are available through take_pic."
                if on_demand
                else "Camera images arrive with each observation."
            ),
            turns=(
                "Respond with one motion tool call, optionally followed by take_pic, "
                "or take_pic alone, or one locate_pixels query."
                if on_demand
                else "Respond with exactly one tool call per turn."
            ),
        )
        # Preserve pre-check guidance, embodiment docs, and frozen skill byte for byte.
        message["content"] = replacement + message["content"][len(original_prefix) :]
