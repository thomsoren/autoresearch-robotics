"""Terminal console: natural-language input, translated into LIBERO robot tool calls.

    uv run -m harness.repl --task 0 --state 0 --fast

One `Robot` and one conversation for the whole console, so the agent keeps the scene
in context across turns. Everything runs on one thread, which is what `Robot`'s
OpenGL context requires.
"""

import argparse
import json
import sys
import time

import anthropic

from harness.agent import FAST_MODE_MODELS, RobotAgent
from harness.env import DEFAULT_PATH as DEFAULT_ENV_PATH
from harness.env import load_dotenv
from harness.prompt import build_prompt
from harness.tools import TOOL_DEFS

BANNER = """\
Robot console. Type an instruction, or /help for commands.
Task: {instruction}
"""

HELP = """\
  /help     this message
  /status   episode result so far (steps, success, termination)
  /budget   remaining control steps
  /usage    tokens and requests so far
  /timing   where the wall clock went: model vs simulator
  /quit     end the session
Anything else is sent to the agent.
"""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="harness.repl", description=__doc__)
    parser.add_argument("--suite", default="libero_goal")
    parser.add_argument("--task", type=int, default=0, help="task id within the suite")
    parser.add_argument("--state", type=int, default=0, help="initial state id")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=500, help="control-step budget")
    parser.add_argument("--output", default=None, help="run directory (must not exist)")
    parser.add_argument(
        "--privileged",
        action="store_true",
        help="also expose object poses; label such runs as state-assisted",
    )
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument(
        "--fast",
        action="store_true",
        help=f"fast mode: up to 2.5x output speed at premium pricing ({', '.join(FAST_MODE_MODELS)} only)",
    )
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=None,
        help="thinking depth and token spend (API default: high)",
    )
    parser.add_argument("--max-turns", type=int, default=40, help="model turns per instruction")
    parser.add_argument("--quiet", action="store_true", help="show only the tool trace")
    return parser.parse_args(argv)


def log_event(path, kind, payload, started):
    """Append one event with both a wall-clock stamp and seconds since the run began."""
    with path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "elapsed": round(time.monotonic() - started, 3),
                    "kind": kind,
                    "payload": payload,
                },
                default=str,
            )
            + "\n"
        )


def rounded(timing):
    """The timing dict with every second rounded to something readable."""
    out = {}
    for key, value in timing.items():
        if isinstance(value, dict):
            out[key] = rounded(value)
        else:
            out[key] = round(value, 3) if isinstance(value, float) else value
    return out


def timing_line(before, after):
    """One line splitting an instruction's wall clock into model and simulator."""
    wall, api, sim, encode = (
        after[key] - before[key]
        for key in ("wall_seconds", "api_seconds", "sim_seconds", "encode_seconds")
    )
    requests = after["api_calls"] - before["api_calls"]
    calls = after["tool_calls"] - before["tool_calls"]
    return (
        f"  ⏱ {wall:.1f}s total = {api:.1f}s model ({requests} request{'s'[: requests != 1]}) "
        f"+ {sim:.1f}s sim ({calls} tool call{'s'[: calls != 1]}) "
        f"+ {encode:.1f}s image encode + {wall - api - sim - encode:.1f}s other"
    )


def render(agent, log_path, message, quiet, started):
    """Print one instruction's events and log all of them."""
    before = dict(agent.timing)
    for kind, payload in agent.send(message):
        log_event(log_path, kind, payload, started)
        if kind == "text" and not quiet:
            print(payload)
        elif kind == "api":
            print(f"  ~ model {payload:.1f}s")
        elif kind == "tool_use":
            name, args = payload
            print(f"  → {name}({json.dumps(args)})")
        elif kind == "tool_result":
            name, summary, is_error, seconds = payload
            print(f"  {'!' if is_error else ' '} {summary} [{seconds:.1f}s]".rstrip())
        elif kind == "stop" and payload not in ("end_turn", "episode_done"):
            print(f"  [stopped: {payload}]")
    print(timing_line(before, agent.timing))


def run(args):
    from simulation.sim import Robot

    output = args.output or f"runs/repl-{time.strftime('%Y%m%d-%H%M%S')}"
    started = time.monotonic()
    with Robot(
        suite=args.suite,
        task_id=args.task,
        init_state_id=args.state,
        seed=args.seed,
        max_steps=args.max_steps,
        output=output,
        privileged=args.privileged,
    ) as robot:
        run_dir = robot.output
        log_path = run_dir / "harness_log.jsonl"
        system_prompt = build_prompt(robot.metadata)
        agent = RobotAgent(
            robot,
            system_prompt,
            model=args.model,
            fast=args.fast,
            effort=args.effort,
            max_turns=args.max_turns,
        )
        if agent.fast_disabled_reason:
            print(f"Note: {agent.fast_disabled_reason}")

        print(BANNER.format(instruction=robot.metadata["instruction"]))
        speed = "fast" if agent.fast else "standard"
        print(f"Model: {args.model} ({speed})   Run directory: {run_dir}\n")

        while not robot.done:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/help":
                print(HELP)
                continue
            if line == "/status":
                print(json.dumps(robot.result(), indent=2))
                continue
            if line == "/budget":
                print(f"{robot.max_steps - robot.steps} of {robot.max_steps} control steps left")
                continue
            if line == "/usage":
                print(json.dumps(agent.usage, indent=2))
                continue
            if line == "/timing":
                print(json.dumps(rounded(agent.timing), indent=2))
                continue
            try:
                render(agent, log_path, line, args.quiet, started)
            except anthropic.APIStatusError as exc:
                print(f"  ! API error {exc.status_code}: {exc.message}", file=sys.stderr)
            except anthropic.APIConnectionError:
                print("  ! network error reaching the API", file=sys.stderr)

        if robot.done:
            print("\nEpisode finished (success or step budget exhausted).")
        result = robot.result()
        (run_dir / "session.json").write_text(
            json.dumps(
                {
                    "model": args.model,
                    "anthropic_sdk_version": anthropic.__version__,
                    "fast_mode": agent.fast,
                    "fast_mode_note": agent.fast_disabled_reason,
                    "effort": args.effort,
                    "max_turns": args.max_turns,
                    "system_prompt": system_prompt,
                    "tools": [tool["name"] for tool in TOOL_DEFS],
                    "usage": agent.usage,
                    "timing": rounded(agent.timing),
                    "episode": result,
                },
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(result, indent=2))
        print(f"\nVideo:   {run_dir / 'episode.mp4'}")
        print(f"Actions: {run_dir / 'actions.jsonl'}")
        print(f"Log:     {log_path}")
        print(f"Session: {run_dir / 'session.json'}")


def main(argv=None):
    args = parse_args(argv)
    loaded = load_dotenv()  # repo-root .env when present; silent no-op otherwise
    if loaded:
        print(f"Loaded from {DEFAULT_ENV_PATH}: {', '.join(sorted(loaded))}")
    try:
        run(args)
    except anthropic.AuthenticationError:
        print(
            "No valid Anthropic credential. Set ANTHROPIC_API_KEY in .env "
            "(cp .env.example .env) or export it in your shell.",
            file=sys.stderr,
        )
        return 1
    except FileExistsError as exc:
        print(f"Run directory already exists: {exc}. Pass a fresh --output.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
