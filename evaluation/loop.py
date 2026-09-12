"""Bounded Inspect executor → SDK skill revision → LIBERO selection loop."""

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from evaluation.evaluate import compare_runs, load_batch, skill_hash, validate_protocol
from evaluation.improve import validate_report

ROOT = Path(__file__).resolve().parents[1]
STATES = (0, 1, 2)


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    """Detect code/dependency changes while a loop is running."""
    import inspect_robots
    import inspect_robots_agent

    paths = [
        ROOT / name
        for name in (
            "execute/inspect_agent.py",
            "execute/control.py",
            "execute/operational_policy.py",
            "execute/perception.py",
            "execute/reactive_controller.py",
            "simulation/sim.py",
            "evaluation/evaluate.py",
            "evaluation/loop.py",
            "evaluation/transfer.py",
            "evaluation/improve.py",
            "evaluation/program.md",
            "uv.lock",
        )
    ]
    for module in (inspect_robots, inspect_robots_agent):
        paths.extend(sorted(Path(module.__file__).parent.rglob("*.py")))
    return {str(p): digest(p) for p in paths}


def profile(args):
    # Public policy lifecycle constructs the actual base prompt without API calls
    # or creating a simulator. The executor itself remains unchanged.
    from inspect_robots import Scene

    from execute.control import pose_precheck
    from execute.inspect_agent import LiberoEmbodiment, image_horizon, output_token_limit
    from execute.operational_policy import OperationalPolicy

    policy = OperationalPolicy(
        model=args.model,
        wire="messages",
        speed=None,
        effort=args.effort,
        max_output_tokens=output_token_limit(args.model),
        max_llm_calls=args.max_calls,
        images="always",
        depth="off",
        image_horizon=image_horizon(args.model),
        wire_capture=False,
        base_url="https://api.anthropic.com/v1",
        api_key_env="CLAUDE_API_KEY",
        env={"CLAUDE_API_KEY": "offline-prompt-inspection"},
        pre_check=pose_precheck if args.control == "pose" else None,
    )
    policy.bind(LiberoEmbodiment(Path("unused"), control=args.control).info)
    policy.reset(Scene(id="profile", instruction="profile"))
    base = policy.transcript()[0]["content"]
    code = identity()
    config = {
        k: getattr(args, k)
        for k in (
            "model",
            "effort",
            "max_calls",
            "max_steps",
            "episode_timeout",
            "suite",
            "task_id",
            "seed",
            "control",
        )
    }
    protocol = {
        "model": args.model,
        "base_prompt_sha256": hashlib.sha256(base.encode()).hexdigest(),
        "executor_revision": hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest(),
        "executor_config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "max_turns": args.max_calls,
        "max_api_requests": args.max_calls,
        "max_budget_usd": None,
        "timeout_seconds": args.episode_timeout,
        "fresh_session_per_episode": True,
    }
    validate_protocol(protocol)
    return {
        "protocol": protocol,
        "identity": code,
        "base_prompt": base,
        "executor_config": config,
    }


def child(command, log, timeout):
    """Isolated process group: also stop SDK/Claude descendants on timeout."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as stream:
        environment = os.environ.copy()
        inherited_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(ROOT) + (
            os.pathsep + inherited_pythonpath if inherited_pythonpath else ""
        )
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=environment,
        )
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def allowance(deadline, required):
    # Never shorten just one episode's declared budget to fit the loop deadline.
    remaining = deadline - time.monotonic()
    if remaining < required:
        raise TimeoutError("Insufficient loop time for the next complete phase")
    return required


def unchanged(frozen):
    if identity() != frozen["identity"]:
        raise ValueError("Code or dependencies changed during the loop")


def batch(args, output, skill, frozen, deadline, states=STATES, split="development"):
    if split not in ("development", "held_out"):
        raise ValueError("Batch split must be development or held_out")
    unchanged(frozen)
    output.mkdir(parents=True, exist_ok=False)
    snapshot = None
    if skill is not None:
        snapshot = output / "skills/task/SKILL.md"
        snapshot.parent.mkdir(parents=True)
        shutil.copyfile(skill, snapshot)
    fingerprint = skill_hash(output / "skills" if snapshot else None)
    manifest = {
        "policy": "noop" if args.smoke else "inspect-robots-agent",
        "suite": args.suite,
        "task_id": args.task_id,
        "state_ids": list(states),
        "seed": args.seed,
        "episode_seeds": {},
        "initial_frame_sha256": {},
        "max_steps": args.max_steps,
        "skill_hash": fingerprint,
        "observation_mode": "calibrated_rgbd",
        "control": args.control,
        "evaluation_split": split,
        "executor_protocol": frozen["protocol"],
        "simulator_sha256": frozen["identity"][str(ROOT / "simulation/sim.py")],
        "evaluator_sha256": frozen["identity"][str(ROOT / "evaluation/evaluate.py")],
        "dependency_lock_sha256": frozen["identity"][str(ROOT / "uv.lock")],
    }
    save(output / "manifest.json", manifest)
    rows = []
    for state in states:
        unchanged(frozen)
        timeout = allowance(deadline, args.episode_timeout + 30)
        raw = output / "raw" / f"state-{state:03d}"
        command = [
            sys.executable,
            "-m",
            "execute.inspect_agent",
            "--output",
            str(raw),
            "--suite",
            args.suite,
            "--task-id",
            str(args.task_id),
            "--state",
            str(state),
            "--seed",
            str(args.seed),
            "--max-steps",
            str(args.max_steps),
            "--max-calls",
            str(args.max_calls),
            "--timeout",
            str(args.episode_timeout),
            "--model",
            args.model,
            "--effort",
            args.effort,
            "--speed",
            "standard",
            "--control",
            args.control,
        ]
        if snapshot:
            command.extend(["--skill", str(snapshot)])
        if args.smoke:
            command.append("--smoke")
        code = child(command, output / f"state-{state:03d}.log", timeout)
        episode = raw / f"state-{state:03d}"
        if not (episode / "result.json").is_file():
            raise RuntimeError(
                f"Executor did not export state {state}; see {output}/state-{state:03d}.log"
            )
        row, config = read(episode / "result.json"), read(raw / "experiment.json")
        if row.get("evaluation_split") not in (None, split):
            raise ValueError("Executor export has a mismatched evaluation split")
        row["evaluation_split"] = split
        save(episode / "result.json", row)
        expected = {
            "model": args.model,
            "speed": "standard",
            "effort": args.effort,
            "max_api_requests": args.max_calls,
            "max_steps": args.max_steps,
            "timeout_seconds": args.episode_timeout,
            "suite": args.suite,
            "task_id": args.task_id,
            "seed": args.seed,
            "state_id": state,
            "control": args.control,
            "rotation": "rot6d" if args.control == "pose" else "fixed",
            "smoke": args.smoke,
            "skill_hash": fingerprint,
            "adapter_sha256": frozen["identity"][str(ROOT / "execute/inspect_agent.py")],
        }
        if any(config.get(k) != v for k, v in expected.items()):
            raise ValueError("Executor export disagrees with the frozen configuration")
        for key in (
            "suite",
            "task_id",
            "max_steps",
            "observation_mode",
            "policy",
            "skill_hash",
            "control",
            "evaluation_split",
        ):
            if row.get(key) != manifest[key]:
                raise ValueError(f"Episode has mismatched {key}")
        if row.get("init_state_id") != state or type(row.get("seed")) is not int:
            raise ValueError("Episode has invalid state/seed")
        if code != 0 and not row.get("error"):
            raise RuntimeError(
                "Executor process failed after exporting an episode; batch incomplete"
            )
        if row.get("error") and row["success"]:
            raise ValueError("Errored episode cannot score successful")
        if row.get("api_requests_attempted", 0) > args.max_calls:
            raise ValueError("Executor exceeded request budget")
        if not args.smoke:
            trace = read(raw / "executor-trace.json")
            system = next((m.get("content") for m in trace if m["role"] == "system"), None)
            # An API error before bind/reset may have no prompt. It scores failure;
            # successful/ordinary failed episodes must establish prompt provenance.
            if not row.get("error") or system is not None:
                if not isinstance(system, str) or not system.startswith(frozen["base_prompt"]):
                    raise ValueError("Executor base prompt differs from the frozen prompt")
                if snapshot and snapshot.read_text() not in system:
                    raise ValueError("Full skill text was not supplied to the executor")
                if not snapshot and system != frozen["base_prompt"]:
                    raise ValueError("Unexpected notes in the no-skill baseline")
        if skill_hash(output / "skills" if snapshot else None) != fingerprint:
            raise ValueError("Batch skill snapshot was modified")
        if skill_hash(raw / "skills" if snapshot else None) != fingerprint:
            raise ValueError("Executor skill snapshot differs from batch")
        target = output / f"state-{state:03d}"
        shutil.copytree(episode, target)  # Preserve the original native export under raw/.
        trace_path = raw / "executor-trace.json"
        if trace_path.exists():
            shutil.copyfile(trace_path, target / "executor-trace.json")
        rows.append(row)
        manifest["episode_seeds"][str(state)] = row["seed"]
        manifest["initial_frame_sha256"][str(state)] = {
            camera: digest(episode / f"0000-{camera}.png")
            for camera in ("agentview", "robot0_eye_in_hand")
        }
        save(output / "manifest.json", manifest)
        with (output / "results.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        print(
            f"{output.name} state={state} success={row['success']} steps={row['steps']}", flush=True
        )
    unchanged(frozen)
    summary = {
        **manifest,
        "episodes": len(rows),
        "successes": sum(r["success"] for r in rows),
        "success_rate": sum(r["success"] for r in rows) / len(rows),
        "policy_errors": sum(r.get("error") is not None for r in rows),
        "mean_steps": sum(r["steps"] for r in rows) / len(rows),
    }
    save(output / "summary.json", summary)
    load_batch(output)
    return output


def successful_episode(batch_path):
    """Return one measured successful development episode, if the incumbent has one."""
    _, _, rows = load_batch(batch_path)
    row = next((item for item in rows if item["success"] and not item.get("error")), None)
    return None if row is None else batch_path / f"state-{row['init_state_id']:03d}"


def propose(args, source, incumbent, output, history, frozen, deadline, iteration):
    unchanged(frozen)
    _, _, rows = load_batch(source)
    failures = [r for r in rows if not r["success"] and not r.get("error")]
    if not failures:
        return None  # Infrastructure errors must not become learned task advice.
    row = failures[(iteration - 1) % len(failures)]
    episode = source / f"state-{row['init_state_id']:03d}"
    context = output.parent / "context.json"
    regression = successful_episode(incumbent)
    save(
        context,
        {
            "tested_batch": str(source),
            "selected_incumbent_batch": str(incumbent),
            "regression_episode": str(regression) if regression else None,
            "selection_history": history,
        },
    )
    timeout = allowance(deadline, args.improve_timeout + 30)
    command = [
        sys.executable,
        "-m",
        "evaluation.improve",
        str(episode),
        "--kind",
        "agent",
        "--trace",
        str(episode / "executor-trace.json"),
        "--context",
        str(context),
        "--output",
        str(output),
        "--model",
        args.improve_model,
        "--max-budget-usd",
        str(args.improve_budget_usd),
        "--max-turns",
        str(args.improve_turns),
        "--timeout",
        str(args.improve_timeout),
    ]
    if (source / "skills").exists():
        command.extend(["--skills", str(source / "skills")])
    if (incumbent / "skills").exists():
        command.extend(["--incumbent", str(incumbent / "skills")])
    if regression is not None:
        command.extend(["--regression", str(regression)])
    if child(command, output.parent / "improvement.log", timeout) != 0:
        raise RuntimeError(f"Improvement agent failed; see {output.parent}/improvement.log")
    unchanged(frozen)
    report, evidence = read(output / "improvement.json"), read(output / "evidence.json")
    validate_report({k: v for k, v in report.items() if k != "validation_status"}, evidence)
    for name, expected in evidence["files_sha256"].items():
        if digest(output / "evidence" / name) != expected:
            raise ValueError("Improvement evidence was modified")
    if report["decision"] == "defer":
        return None
    skill = output / "skills" / report["candidate_name"] / "SKILL.md"
    if skill.is_symlink() or skill.read_text() != report["candidate_markdown"]:
        raise ValueError("Candidate skill differs from the structured proposal")
    if len(skill.read_text()) > 16000:
        raise ValueError("Candidate exceeds the loop's 16000-character skill limit")
    return skill


def run(args):
    started = time.monotonic()
    deadline = started + args.max_seconds
    output = args.output.resolve()
    if args.skill and (args.skill.is_symlink() or not args.skill.is_file()):
        raise ValueError("Initial skill must be a regular Markdown file")
    output.mkdir(parents=True, exist_ok=False)
    frozen = profile(args)
    save(output / "protocol.json", frozen)
    state = {
        "status": "running",
        "selected_batch": None,
        "selected_skill": None,
        "iterations": [],
        "active_skills_updated": False,
        "fixture": args.smoke,
        "limits": {
            "iterations": args.iterations,
            "wall_seconds": args.max_seconds,
            "max_executor_requests": 3 * (1 + args.iterations) * args.max_calls,
            "max_improvement_budget_usd": args.iterations * args.improve_budget_usd,
            "executor_dollar_cap": None,
        },
    }
    save(output / "loop.json", state)
    try:
        selected = source = batch(args, output / "baseline", args.skill, frozen, deadline)
        state["selected_batch"] = str(selected)
        state["selected_skill"] = str(selected / "skills/task/SKILL.md") if args.skill else None
        save(output / "loop.json", state)
        for iteration in range(1, args.iterations + 1):
            if args.smoke:
                state["status"] = "smoke_complete"
                break
            if load_batch(selected)[1]["successes"] == len(STATES):
                state["status"] = "development_solved"
                break
            folder = output / f"iteration-{iteration:03d}"
            history = {"selected_batch": state["selected_batch"], "iterations": state["iterations"]}
            skill = propose(
                args,
                source,
                selected,
                folder / "improvement",
                history,
                frozen,
                deadline,
                iteration,
            )
            if skill is None:
                state["status"] = "deferred"
                break
            candidate = batch(args, folder / "candidate", skill, frozen, deadline)
            # Verify incumbent again and compare only complete batches.
            unchanged(frozen)
            comparison = compare_runs(selected, candidate, output=folder / "comparison.json")
            state["iterations"].append({"iteration": iteration, **comparison})
            if comparison["decision"] == "keep":
                selected = candidate
                state["selected_batch"] = str(selected)
                state["selected_skill"] = str(selected / "skills/task/SKILL.md")
            source = candidate  # Rejected failures inform the next proposal, never promotion.
            save(output / "loop.json", state)
            if comparison["decision"] == "inconclusive":
                state["status"] = "inconclusive"
                break
        else:
            state["status"] = (
                "development_solved"
                if load_batch(selected)[1]["successes"] == len(STATES)
                else "iteration_limit"
            )
    except (TimeoutError, subprocess.TimeoutExpired):
        state["status"] = "time_limit"
    except KeyboardInterrupt:
        state["status"] = "interrupted"
    except Exception as exc:
        # Detailed subprocess diagnostics stay in local logs, not the run summary.
        state.update(status="error", error_type=type(exc).__name__)
        raise
    finally:
        state["elapsed_seconds"] = time.monotonic() - started
        save(output / "loop.json", state)
    print(f"Loop: {state['status']}; selected skill: {state['selected_skill']}", flush=True)
    return state


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runs" / f"loop-{time.time_ns()}")
    parser.add_argument("--skill", type=Path, help="Optional initial task SKILL.md")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=3600)
    parser.add_argument("--suite", default="libero_goal")
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--control", choices=["pose", "xyz"], default="pose")
    parser.add_argument("--model", default="claude-fable-5-1")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-steps", type=int, default=900)
    parser.add_argument("--episode-timeout", type=float, default=300)
    parser.add_argument("--improve-model", default="claude-opus-5")
    parser.add_argument("--improve-budget-usd", type=float, default=1)
    parser.add_argument("--improve-turns", type=int, default=18)
    parser.add_argument("--improve-timeout", type=float, default=240)
    parser.add_argument(
        "--smoke", action="store_true", help="Three real simulator fixtures; no API or selection"
    )
    args = parser.parse_args(argv)
    for name in (
        "iterations",
        "max_seconds",
        "max_calls",
        "max_steps",
        "episode_timeout",
        "improve_budget_usd",
        "improve_turns",
        "improve_timeout",
    ):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{name} must be positive and finite")
    if args.skill:
        args.skill = args.skill.resolve()
        if args.skill.suffix != ".md" or args.output.resolve().is_relative_to(args.skill.parent):
            parser.error("Use a Markdown skill and an output outside its directory")
    return args


if __name__ == "__main__":
    run(arguments())
