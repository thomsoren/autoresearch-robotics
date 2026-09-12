"""Inspect one development episode with a separate Claude Agent SDK session."""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import imageio.v2 as imageio
import jsonschema
import yaml
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from dotenv import dotenv_values
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
NUMBER = r"[+\-\N{MINUS SIGN}]?(?:\d+(?:\.\d*)?|\.\d+)"
COORDINATE_TRIPLET = re.compile(rf"[\[(]\s*{NUMBER}\s*,\s*{NUMBER}\s*,\s*{NUMBER}\s*[\])]")
NUMERIC_AXIS_ASSIGNMENT = re.compile(rf"\b[xyz]\s*(?:=|:|≈|~)\s*{NUMBER}\b", re.IGNORECASE)
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["propose", "defer"]},
        "diagnosis": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "array", "items": {"type": "string"}},
        "prediction": {"type": "string"},
        "candidate_name": {"type": "string", "enum": ["", "robot_operating_guide"]},
        "candidate_markdown": {"type": "string"},
    },
    "required": [
        "decision",
        "diagnosis",
        "evidence",
        "uncertainty",
        "prediction",
        "candidate_name",
        "candidate_markdown",
    ],
    "additionalProperties": False,
}


def api_key(root=ROOT):
    # Read one value without changing the parent process or exposing the .env file.
    key = os.environ.get("CLAUDE_API_KEY") or dotenv_values(root / ".env", interpolate=False).get(
        "CLAUDE_API_KEY"
    )
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Set CLAUDE_API_KEY in the environment or repository .env")
    return key.strip()


def prepare_evidence(
    episode,
    output,
    kind,
    skills=None,
    trace=None,
    context=None,
    regression=None,
    incumbent=None,
):
    episode, output = Path(episode).resolve(), Path(output).resolve()
    if kind not in ("smoke", "agent"):
        raise ValueError("kind must be smoke or agent")
    result = json.loads((episode / "result.json").read_text())
    if type(result.get("init_state_id")) is not int or result["init_state_id"] not in (0, 1, 2):
        raise ValueError("Only development initial states 0, 1, 2 may enter improvement")
    if result.get("evaluation_split", "development") != "development":
        raise ValueError("Only the development split may enter improvement")
    if type(result.get("success")) is not bool:
        raise ValueError("Episode result must contain a boolean benchmark success")
    sources = [
        (p.name, p)
        for p in episode.iterdir()
        if p.name
        in ("episode.json", "result.json", "actions.jsonl", "control.jsonl", "episode.mp4")
        or re.fullmatch(r"\d+-(agentview|robot0_eye_in_hand)\.png", p.name)
    ]
    regression_result = None
    if regression is not None:
        regression = Path(regression).resolve()
        if not regression.is_dir():
            raise ValueError("Regression must be an existing episode directory")
        regression_result = json.loads((regression / "result.json").read_text())
        if type(regression_result.get("init_state_id")) is not int or regression_result[
            "init_state_id"
        ] not in (0, 1, 2):
            raise ValueError("Only development initial states 0, 1, 2 may enter improvement")
        if regression_result.get("evaluation_split", "development") != "development":
            raise ValueError("Only the development split may enter improvement")
        if regression_result.get("success") is not True:
            raise ValueError("Regression episode must have benchmark success true")
        if regression_result.get("error") is not None or regression_result.get("policy") == "noop":
            raise ValueError("Regression must be an error-free agent episode")
        sources.extend(
            ("regression/" + p.name, p)
            for p in regression.iterdir()
            if p.name
            in (
                "episode.json",
                "result.json",
                "actions.jsonl",
                "control.jsonl",
                "executor-trace.json",
                "episode.mp4",
            )
            or re.fullmatch(r"\d+-(agentview|robot0_eye_in_hand)\.png", p.name)
        )
    if trace is not None:
        sources.append(("executor-trace.txt", Path(trace).absolute()))
    if context is not None:
        sources.append(("loop-context.json", Path(context).absolute()))
    tested_skill_files = []
    if skills is not None:
        skills = Path(skills).resolve()
        if not skills.is_dir():
            raise ValueError("Skills must be an existing directory")
        tested_skill_files = [
            "skills/" + p.relative_to(skills).as_posix() for p in sorted(skills.rglob("*.md"))
        ]
        sources.extend(zip(tested_skill_files, sorted(skills.rglob("*.md"))))
    incumbent_skill_files = []
    if incumbent is not None:
        incumbent = Path(incumbent).resolve()
        if not incumbent.is_dir():
            raise ValueError("Incumbent must be an existing directory")
        incumbent_paths = sorted(incumbent.rglob("*.md"))
        incumbent_skill_files = [
            "incumbent/" + p.relative_to(incumbent).as_posix() for p in incumbent_paths
        ]
        sources.extend(zip(incumbent_skill_files, incumbent_paths))
    if (
        output.is_relative_to(episode)
        or (regression and output.is_relative_to(regression))
        or (skills and output.is_relative_to(skills))
        or (incumbent and output.is_relative_to(incumbent))
    ):
        raise ValueError("Output must be outside supplied evidence sources")
    for _, path in sources:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Evidence must be regular files, not symlinks")
    output.mkdir(parents=True, exist_ok=False)
    index = {}
    for name, path in sources:
        target = output / "evidence" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        index[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        "source_episode": str(episode),
        "kind": kind,
        "split": "development",
        "fixture": kind == "smoke" or result.get("policy") == "noop",
        "benchmark": result,
        "regression": regression_result,
        "tested_skill_files": tested_skill_files,
        "incumbent_skill_files": incumbent_skill_files,
        "files_sha256": index,
        "missing_evidence": ["Measured joint displacement/contact forces are not supplied."]
        + ([] if trace else ["No executor trace: tool returns and loaded skills are unknown."])
        + ([] if skills else ["No tested skill snapshot supplied."])
        + ([] if regression else ["No successful regression episode supplied."])
        + ([] if incumbent else ["No selected incumbent snapshot supplied."]),
    }
    (output / "evidence.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def read_evidence(output, index, name, start=1, count=100):
    if name not in index or Path(name).suffix not in (".json", ".jsonl", ".md", ".txt"):
        raise ValueError("Choose an indexed text artifact")
    if type(start) is not int or start < 1 or type(count) is not int or not 1 <= count <= 200:
        raise ValueError("start must be >= 1; count must be 1..200")
    lines = (output / "evidence" / name).read_text().splitlines()
    selected = "\n".join(
        f"{i + start}: {s}" for i, s in enumerate(lines[start - 1 : start - 1 + count])
    )
    return f"{name}, total lines={len(lines)}\n" + selected[:30000]


def frame_content(output, index, name, frame=0):
    if name not in index or Path(name).suffix not in (".png", ".mp4"):
        raise ValueError("Choose an indexed image or video")
    if type(frame) is not int or not 0 <= frame <= 10000:
        raise ValueError("frame must be a nonnegative integer <= 10000")
    path = output / "evidence" / name
    if path.suffix == ".mp4":
        with imageio.get_reader(str(path)) as reader:
            picture = Image.fromarray(reader.get_data(frame))
            caption = f"{name}: frame {frame}, playback fps={reader.get_meta_data()['fps']}"
    else:
        if frame != 0:
            raise ValueError("Use frame=0 for a still image")
        picture = Image.open(path)
        caption = name
    with picture:
        picture.thumbnail((768, 768))
        buffer = io.BytesIO()
        picture.convert("RGB").save(buffer, format="JPEG")
    return [
        {"type": "text", "text": caption},
        {
            "type": "image",
            "mimeType": "image/jpeg",
            "data": base64.b64encode(buffer.getvalue()).decode(),
        },
    ]


def validate_report(report, manifest):
    if report.get("candidate_name") not in ("", "robot_operating_guide"):
        raise ValueError("A proposal must update robot_operating_guide")
    jsonschema.validate(report, SCHEMA)
    if report["decision"] == "defer":
        if report["candidate_name"] or report["candidate_markdown"]:
            raise ValueError("A deferred diagnosis must not contain a candidate")
        return
    if manifest["fixture"] or manifest["benchmark"].get("error"):
        raise ValueError("Smoke/no-op or errored episodes cannot generate a skill candidate")
    if not report["evidence"] or not report["prediction"].strip():
        raise ValueError("A proposal needs evidence and a testable prediction")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", report["candidate_name"]):
        raise ValueError("Candidate name must be a lowercase skill identifier")
    markdown = report["candidate_markdown"]
    if len(markdown) > 16000:
        raise ValueError("Candidate must be at most 16000 characters")
    if (
        "```" in markdown
        or re.search(r"(?m)^\s*~~~", markdown)
        or COORDINATE_TRIPLET.search(markdown)
        or NUMERIC_AXIS_ASSIGNMENT.search(markdown)
    ):
        raise ValueError("Candidate must use prose without fenced code or coordinate recipes")
    if not markdown.startswith("---\n") or "\n---\n" not in markdown[4:]:
        raise ValueError("Candidate must have YAML frontmatter")
    metadata = yaml.safe_load(markdown[4:].split("\n---\n", 1)[0])
    if not isinstance(metadata, dict) or metadata.get("name") != report["candidate_name"]:
        raise ValueError("Candidate frontmatter name must match its identifier")
    if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
        raise ValueError("Candidate needs a description")
    if not markdown[4:].split("\n---\n", 1)[1].strip():
        raise ValueError("Candidate needs a procedure after its frontmatter")


def sdk_options(key, cwd, config_dir, model, budget, turns, server, prompt):
    return ClaudeAgentOptions(
        model=model,
        system_prompt=prompt,
        cwd=str(cwd),
        tools=[],
        skills=[],
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={"evidence": server},
        allowed_tools=["mcp__evidence__read_evidence", "mcp__evidence__view_frame"],
        permission_mode="dontAsk",
        max_turns=turns,
        max_budget_usd=budget,
        output_format={"type": "json_schema", "schema": SCHEMA},
        env={
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_CODE_USE_BEDROCK": "0",
            "CLAUDE_CODE_USE_VERTEX": "0",
            "CLAUDE_CODE_USE_FOUNDRY": "0",
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        },
    )


async def run_agent(output, manifest, key, model="sonnet", budget=0.5, turns=8, timeout=120):
    index = manifest["files_sha256"]
    prompt = Path(__file__).with_name("program.md").read_text()
    (output / "program.md").write_text(prompt)
    log = output / "agent.jsonl"

    def record(event):
        with log.open("a") as stream:
            stream.write(json.dumps(event).replace(key, "[REDACTED]") + "\n")

    @tool(
        "read_evidence",
        "Read numbered lines of an indexed evidence text file.",
        {"name": str, "start": int, "count": int},
    )
    async def read(args):
        try:
            result = read_evidence(output, index, args["name"], args["start"], args["count"])
            record({"event": "evidence_read", "arguments": args})
            return {"content": [{"type": "text", "text": result}]}
        except (ValueError, OSError, KeyError) as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    @tool(
        "view_frame",
        "View an indexed PNG (frame=0) or zero-based MP4 frame as an image.",
        {"name": str, "frame": int},
    )
    async def view(args):
        try:
            content = frame_content(output, index, args["name"], args["frame"])
            record({"event": "frame_viewed", "arguments": args})
            return {"content": content}
        except (ValueError, OSError, KeyError, IndexError) as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    server = create_sdk_mcp_server(name="evidence", tools=[read, view])
    terminal = None
    models = set()
    with tempfile.TemporaryDirectory(prefix="robot-evaluation-") as isolated:
        cwd = Path(isolated) / "work"
        cwd.mkdir()
        options = sdk_options(
            key, cwd, Path(isolated) / "config", model, budget, turns, server, prompt
        )

        async with asyncio.timeout(timeout):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(
                    "Inspect this development episode. Read its actions and inspect "
                    "at least one image/video frame before reporting.\n" + json.dumps(manifest)
                )
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        models.add(message.model)
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                record({"event": "assistant", "text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                record(
                                    {
                                        "event": "tool_call",
                                        "name": block.name,
                                        "arguments": block.input,
                                    }
                                )
                    elif isinstance(message, ResultMessage):
                        terminal = message
    if terminal is None:
        raise RuntimeError("SDK returned no terminal result")
    usage = {
        "sdk_version": version("claude-agent-sdk"),
        "requested_model": model,
        "observed_models": sorted(models),
        "cost_usd": terminal.total_cost_usd,
        "usage": terminal.usage,
        "turns": terminal.num_turns,
        "session_id": terminal.session_id,
        "subtype": terminal.subtype,
        "is_error": terminal.is_error,
        "budget_usd": budget,
        "timeout_seconds": timeout,
    }
    (output / "usage.json").write_text(json.dumps(usage, indent=2) + "\n")
    if terminal.is_error or terminal.structured_output is None:
        detail = (terminal.result or terminal.subtype).replace(key, "[REDACTED]")
        raise RuntimeError(f"SDK did not finish a structured report: {detail}")
    report = terminal.structured_output
    validate_report(report, manifest)
    for name, digest in index.items():
        if hashlib.sha256((output / "evidence" / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Evidence changed during diagnosis")
    if report["decision"] == "propose":
        skill = output / "skills" / report["candidate_name"] / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(report["candidate_markdown"])
    report["validation_status"] = "unvalidated"
    (output / "improvement.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    parser.add_argument("--kind", choices=["smoke", "agent"], default="smoke")
    parser.add_argument("--skills", type=Path)
    parser.add_argument("--trace", type=Path, help="Existing executor text/JSON export (kept raw)")
    parser.add_argument("--context", type=Path, help="Loop selection history, indexed as evidence")
    parser.add_argument(
        "--regression", type=Path, help="Successful development episode retained as a reference"
    )
    parser.add_argument(
        "--incumbent", type=Path, help="Selected incumbent skill directory, kept separate"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--max-budget-usd", type=float, default=0.5)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--prepare-only", action="store_true", help="Package evidence without an API call"
    )
    args = parser.parse_args()
    if args.max_turns < 1 or any(
        not math.isfinite(x) or x <= 0 for x in (args.max_budget_usd, args.timeout)
    ):
        parser.error("Budgets, turns and timeout must be positive and finite")
    key = None if args.prepare_only else api_key()
    output = (args.output or ROOT / "runs" / f"improve-{time.time_ns()}").resolve()
    manifest = prepare_evidence(
        args.episode,
        output,
        args.kind,
        args.skills,
        args.trace,
        args.context,
        regression=args.regression,
        incumbent=args.incumbent,
    )
    if not args.prepare_only:
        try:
            report = asyncio.run(
                run_agent(
                    output,
                    manifest,
                    key,
                    args.model,
                    args.max_budget_usd,
                    args.max_turns,
                    args.timeout,
                )
            )
            print(f"Decision: {report['decision']} (unvalidated)")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}".replace(key, "[REDACTED]")
            (output / "error.json").write_text(json.dumps({"error": message}) + "\n")
            raise SystemExit(message) from None
    print(f"Artifacts: {output}")


if __name__ == "__main__":
    main()
