"""Terminal console: natural-language input, translated into LIBERO robot tool calls.

    uv run -m harness.repl --task 0 --state 0

One `Robot` and one SDK session for the whole console, so the agent keeps the scene in
context across turns. All robot calls happen on the event-loop thread that constructed
the robot; only stdin is read on a worker thread.
"""

import argparse
import asyncio
import dataclasses
import json
import sys
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLINotFoundError,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk import __version__ as sdk_version

from harness.env import DEFAULT_PATH as DEFAULT_ENV_PATH
from harness.env import load_dotenv
from harness.prompt import build_prompt
from harness.tools import ALLOWED_TOOLS, build_robot_server

BANNER = """\
Robot console. Type an instruction, or /help for commands.
Task: {instruction}
"""

HELP = """\
  /help     this message
  /status   episode result so far (steps, success, termination)
  /budget   remaining control steps
  /quit     end the session
Anything else is sent to the agent.
"""

FACT_KEYS = ("steps", "remaining_steps", "reached", "position_error", "success", "done")


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
    parser.add_argument("--max-turns", type=int, default=40, help="model turns per instruction")
    parser.add_argument("--max-budget-usd", type=float, default=None)
    return parser.parse_args(argv)


def _scrub(value):
    """Drop base64 image payloads so the log stays readable."""
    if isinstance(value, dict):
        if value.get("type") == "image":
            return {**value, "data": f"<{len(value.get('data', ''))} base64 chars>"}
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _log(path, message):
    record = _scrub(dataclasses.asdict(message) if dataclasses.is_dataclass(message) else message)
    with path.open("a") as stream:
        stream.write(json.dumps({"kind": type(message).__name__, "message": record}, default=str) + "\n")


def _summarize_tool_result(block):
    """One line of the facts a tool returned, ignoring the images."""
    content = block.content if isinstance(block.content, list) else []
    for item in content:
        if item.get("type") != "text":
            continue
        try:
            facts = json.loads(item["text"])
        except (ValueError, KeyError):
            continue
        if not isinstance(facts, dict):
            continue
        parts = [f"{key}={facts[key]}" for key in FACT_KEYS if key in facts]
        return " ".join(parts)
    if isinstance(block.content, str):
        return block.content.strip().splitlines()[0] if block.content.strip() else ""
    return ""


async def _drain(client, log_path, usage):
    """Print one agent response, logging every message."""
    async for message in client.receive_response():
        _log(log_path, message)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(block.text.strip())
                elif isinstance(block, ToolUseBlock):
                    print(f"  → {block.name.split('__')[-1]}({json.dumps(block.input)})")
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    summary = _summarize_tool_result(block)
                    marker = "!" if block.is_error else " "
                    if summary:
                        print(f"  {marker} {summary}")
        elif isinstance(message, ResultMessage):
            usage["turns"] = usage.get("turns", 0) + message.num_turns
            if message.total_cost_usd is not None:
                usage["cost_usd"] = usage.get("cost_usd", 0.0) + message.total_cost_usd
            usage["last_session_id"] = message.session_id
            if message.is_error:
                print(f"  ! session error: {message.subtype} {message.errors or ''}")


async def run(args):
    from simulation.sim import Robot

    output = args.output or f"runs/repl-{time.strftime('%Y%m%d-%H%M%S')}"
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
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=args.model,
            mcp_servers={"robot": build_robot_server(robot)},
            strict_mcp_config=True,
            tools=[],  # no built-in tools; the robot server is the whole surface
            allowed_tools=ALLOWED_TOOLS,
            setting_sources=[],  # no project/user settings, no skill auto-discovery
            skills=None,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget_usd,
            cwd=str(run_dir),
        )

        print(BANNER.format(instruction=robot.metadata["instruction"]))
        print(f"Run directory: {run_dir}\n")
        usage = {}
        loop = asyncio.get_running_loop()

        async with ClaudeSDKClient(options=options) as client:
            while not robot.done:
                try:
                    line = (await loop.run_in_executor(None, input, "> ")).strip()
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
                await client.query(line)
                await _drain(client, log_path, usage)

        if robot.done:
            print("\nEpisode finished (success or step budget exhausted).")
        result = robot.result()
        (run_dir / "session.json").write_text(
            json.dumps(
                {
                    "model": args.model,
                    "sdk_version": sdk_version,
                    "max_turns": args.max_turns,
                    "max_budget_usd": args.max_budget_usd,
                    "system_prompt": system_prompt,
                    "allowed_tools": ALLOWED_TOOLS,
                    "usage": usage,
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
        asyncio.run(run(args))
    except CLINotFoundError:
        print(
            "Claude Code CLI not found. The Agent SDK drives it as a subprocess.\n"
            "Install Node and the CLI, or point ClaudeAgentOptions.cli_path at it.",
            file=sys.stderr,
        )
        return 1
    except FileExistsError as exc:
        print(f"Run directory already exists: {exc}. Pass a fresh --output.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
