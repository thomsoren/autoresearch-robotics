"""Agent loop over the Anthropic Messages API.

Replaces the Claude Agent SDK so the harness can set fast mode, which the Agent SDK
cannot express (its `SdkBeta` allows only the 1M-context flag and it has no `speed`
parameter at all).

Synchronous by design: `Robot` owns an OpenGL context and must be driven from one
thread, and a plain loop keeps every tool call on it.
"""

import json
import time

import anthropic

from harness.tools import TOOL_DEFS, dispatch

FAST_MODE_BETA = "fast-mode-2026-02-01"
FAST_MODE_MODELS = ("claude-opus-5", "claude-opus-4-8")

MAX_TOKENS = 16000

# Facts worth showing on one terminal line, in the order they read best.
FACT_KEYS = ("steps", "remaining_steps", "reached", "position_error", "success", "done")


def supports_fast_mode(model):
    return model in FAST_MODE_MODELS


def summarize(blocks):
    """One line of the facts a tool returned, ignoring the images."""
    for block in blocks:
        if block.get("type") != "text":
            continue
        try:
            facts = json.loads(block["text"])
        except ValueError:
            continue
        if isinstance(facts, dict):
            return " ".join(f"{key}={facts[key]}" for key in FACT_KEYS if key in facts)
    return blocks[0]["text"].splitlines()[0] if blocks else ""


class RobotAgent:
    """One conversation driving one `Robot`.

    `send()` is a generator of `(kind, payload)` events so the caller owns all
    printing: `("text", str)`, `("thinking", str)`, `("api", seconds)`,
    `("tool_use", (name, args))`, `("tool_result", (name, summary, is_error, seconds))`,
    `("stop", reason)`.

    Every event that costs wall-clock time carries its own duration, and `timing`
    accumulates the same numbers for the whole conversation, which answers the
    question the console exists to answer: model latency or simulator.
    """

    def __init__(
        self,
        robot,
        system_prompt,
        model="claude-opus-5",
        fast=False,
        effort=None,
        max_turns=40,
        client=None,
    ):
        self.robot = robot
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.client = client or anthropic.Anthropic()
        self.messages = []
        self.usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
        self.timing = {
            "wall_seconds": 0.0,
            "api_seconds": 0.0,
            "api_calls": 0,
            "tool_seconds": 0.0,
            "tool_calls": 0,
            "sim_seconds": 0.0,
            "encode_seconds": 0.0,
            "per_tool": {},
        }
        self.fast = bool(fast)
        self.fast_disabled_reason = None
        if self.fast and not supports_fast_mode(model):
            self.fast = False
            self.fast_disabled_reason = (
                f"fast mode is only available on {' and '.join(FAST_MODE_MODELS)}; "
                f"{model} runs at standard speed"
            )
        # Cache the stable prefix: the system prompt and tool list never change.
        self.system = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]

    def _request(self):
        params = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": self.system,
            "messages": self.messages,
            "tools": TOOL_DEFS,
        }
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if not self.fast:
            return self.client.messages.create(**params)
        try:
            return self.client.beta.messages.create(
                **params, speed="fast", betas=[FAST_MODE_BETA]
            )
        except anthropic.RateLimitError:
            # Fast mode has its own rate limit. Fall back rather than stall the
            # episode; this invalidates the prompt cache, which is the cheaper loss.
            self.fast = False
            self.fast_disabled_reason = "fast-mode rate limit hit; fell back to standard speed"
            return self.client.messages.create(**params)

    def _record_tool_time(self, name, timing):
        """Fold one dispatch's costs into the running totals."""
        totals = self.timing
        totals["tool_calls"] += 1
        per_tool = totals["per_tool"].setdefault(
            name, {"calls": 0, "seconds": 0.0, "sim_seconds": 0.0, "encode_seconds": 0.0}
        )
        per_tool["calls"] += 1
        for key in ("seconds", "sim_seconds", "encode_seconds"):
            value = timing.get(key, 0.0)
            per_tool[key] += value
            totals["tool_seconds" if key == "seconds" else key] += value

    def _record(self, response):
        usage = response.usage
        self.usage["requests"] += 1
        self.usage["input_tokens"] += usage.input_tokens
        self.usage["output_tokens"] += usage.output_tokens
        for field in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            value = getattr(usage, field, None)
            if value:
                self.usage[field] = self.usage.get(field, 0) + value
        speed = getattr(usage, "speed", None)
        if speed:
            self.usage["speed"] = speed

    def send(self, user_message):
        """Run one instruction to completion, yielding events as they happen."""
        started = time.perf_counter()
        try:
            yield from self._send(user_message)
        finally:
            # Wall clock for the instruction, however it ended: api_seconds plus
            # tool_seconds plus whatever is left over is the whole story.
            self.timing["wall_seconds"] += time.perf_counter() - started

    def _send(self, user_message):
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(self.max_turns):
            api_started = time.perf_counter()
            response = self._request()
            api_seconds = time.perf_counter() - api_started
            self.timing["api_seconds"] += api_seconds
            self.timing["api_calls"] += 1
            self._record(response)
            yield "api", api_seconds

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                yield "stop", f"refusal ({getattr(details, 'category', None)})"
                return

            self.messages.append({"role": "assistant", "content": response.content})

            tool_uses = []
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    yield "text", block.text.strip()
                elif block.type == "thinking" and getattr(block, "thinking", ""):
                    yield "thinking", block.thinking
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    yield "tool_use", (block.name, block.input)

            if response.stop_reason != "tool_use" or not tool_uses:
                yield "stop", response.stop_reason
                return

            # All results for one assistant turn go back in a single user message.
            results = []
            for block in tool_uses:
                timing = {}
                content, is_error = dispatch(self.robot, block.name, block.input, timing)
                self._record_tool_time(block.name, timing)
                yield "tool_result", (block.name, summarize(content), is_error, timing["seconds"])
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            self.messages.append({"role": "user", "content": results})

            if self.robot.done:
                yield "stop", "episode_done"
                return

        yield "stop", "max_turns"
