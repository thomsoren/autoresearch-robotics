"""Run a frozen selected skill and its original baseline on explicit evaluation cases."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import time
from argparse import Namespace
from pathlib import Path

from evaluation import loop

DEVELOPMENT_STATES = frozenset(loop.STATES)
EXECUTOR_KEYS = {
    "model",
    "effort",
    "max_calls",
    "max_steps",
    "episode_timeout",
    "suite",
    "task_id",
    "seed",
    "control",
}


def _inside(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    return path == parent or path.is_relative_to(parent)


def _selected_development(loop_dir, state):
    baseline = (loop_dir / "baseline").resolve()
    if not _inside(baseline, loop_dir):
        raise ValueError("Development baseline must stay inside the loop directory")
    baseline_manifest, _, _ = loop.load_batch(baseline)
    expected = baseline
    for comparison in state.get("iterations", []):
        if comparison.get("decision") == "keep":
            candidate = Path(comparison.get("candidate", "")).resolve()
            if not _inside(candidate, loop_dir):
                raise ValueError("Selected candidate must stay inside the loop directory")
            expected = candidate
    selected = Path(state.get("selected_batch") or "").resolve()
    if selected != expected or not _inside(selected, loop_dir):
        raise ValueError("Loop selected batch disagrees with its development history")
    selected_manifest, _, _ = loop.load_batch(selected)
    if baseline_manifest["state_ids"] != list(loop.STATES) or selected_manifest[
        "state_ids"
    ] != list(loop.STATES):
        raise ValueError("Frozen selection must come from development states [0, 1, 2]")
    skill = selected / "skills/task/SKILL.md"
    selected_skill = state.get("selected_skill")
    if skill.is_file():
        if Path(selected_skill or "").resolve() != skill.resolve():
            raise ValueError("Loop selected skill disagrees with its selected batch")
    elif selected_skill is not None:
        raise ValueError("Loop names a selected skill that is absent from its batch")
    baseline_skill = baseline / "skills/task/SKILL.md"
    return (
        baseline,
        selected,
        baseline_skill if baseline_skill.is_file() else None,
        (skill if skill.is_file() else None),
    )


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _validate_stored_profile(stored):
    if not isinstance(stored, dict) or set(stored) != {
        "protocol",
        "identity",
        "base_prompt",
        "executor_config",
    }:
        raise ValueError("Loop lacks the exact frozen profile")
    protocol, config = stored["protocol"], stored["executor_config"]
    if not isinstance(config, dict) or set(config) != EXECUTOR_KEYS:
        raise ValueError("Loop lacks the exact frozen executor configuration")
    loop.validate_protocol(protocol)
    if _json_hash(config) != protocol["executor_config_sha256"]:
        raise ValueError("Stored executor configuration hash does not match its contents")
    if hashlib.sha256(stored["base_prompt"].encode()).hexdigest() != protocol["base_prompt_sha256"]:
        raise ValueError("Stored base prompt hash does not match its contents")
    if _json_hash(stored["identity"]) != protocol["executor_revision"]:
        raise ValueError("Stored executor revision does not match its identity")
    expected = {
        "model": config["model"],
        "max_turns": config["max_calls"],
        "max_api_requests": config["max_calls"],
        "timeout_seconds": config["episode_timeout"],
    }
    if any(protocol.get(name) != value for name, value in expected.items()):
        raise ValueError("Stored executor configuration disagrees with protocol limits")
    return config


def _executor_args(stored, args):
    values = {**stored, "suite": args.suite, "task_id": args.task_id, "smoke": False}
    return Namespace(**values)


def _condition(batch_path, source_batch):
    manifest, summary, rows = loop.load_batch(batch_path)
    return {
        "batch": str(batch_path),
        "source_development_batch": str(source_batch),
        "skill_hash": manifest["skill_hash"],
        "episodes": summary["episodes"],
        "successes": summary["successes"],
        "success_rate": summary["success_rate"],
        "policy_errors": summary["policy_errors"],
        "mean_steps": summary["mean_steps"],
        "outcomes": rows,
    }


def _validate_held_out_pair(baseline, selected, states):
    """Validate paired evidence without selecting a skill from held-out outcomes."""
    left, _, left_rows = loop.load_batch(baseline)
    right, _, right_rows = loop.load_batch(selected)
    for manifest in (left, right):
        if (
            manifest.get("state_ids") != list(states)
            or manifest.get("evaluation_split") != "held_out"
        ):
            raise ValueError("Held-out batch disagrees with the requested cases")
        for key in ("episode_seeds", "initial_frame_sha256"):
            if not isinstance(manifest.get(key), dict) or not manifest[key]:
                raise ValueError(f"Held-out batch is missing {key}")
    for key in (
        "policy",
        "suite",
        "task_id",
        "seed",
        "max_steps",
        "observation_mode",
        "control",
        "episode_seeds",
        "initial_frame_sha256",
        "executor_protocol",
        "simulator_sha256",
        "evaluator_sha256",
        "dependency_lock_sha256",
    ):
        if left.get(key) is None or left.get(key) != right.get(key):
            raise ValueError(f"Held-out conditions disagree: {key}")
    for manifest, rows in ((left, left_rows), (right, right_rows)):
        for row in rows:
            if row.get("seed") != manifest["episode_seeds"][str(row["init_state_id"])]:
                raise ValueError("Held-out episode disagrees with its recorded seed")


def _snapshot_skill(source, destination, expected_hash):
    if source is None:
        if loop.skill_hash(None) != expected_hash:
            raise ValueError("Development batch is missing its frozen skill snapshot")
        return None
    target = destination / "task/SKILL.md"
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    if loop.skill_hash(destination) != expected_hash:
        raise ValueError("Development skill changed while creating the evaluation snapshot")
    return target


def run(args):
    loop_dir = args.loop.resolve()
    output = args.output.resolve()
    if _inside(output, loop_dir):
        raise ValueError("Frozen evaluation output must be outside the development loop")
    state = loop.read(loop_dir / "loop.json")
    if state.get("status") in (None, "running", "error", "interrupted"):
        raise ValueError("Development loop is not in a usable terminal state")
    if state.get("fixture") or state.get("active_skills_updated") is not False:
        raise ValueError("Frozen evaluation requires a real, non-promoting development loop")
    baseline, selected, baseline_skill, selected_skill = _selected_development(loop_dir, state)
    stored = loop.read(loop_dir / "protocol.json")
    config = _validate_stored_profile(stored)
    baseline_manifest, _, _ = loop.load_batch(baseline)
    selected_manifest, _, _ = loop.load_batch(selected)
    if baseline_manifest.get("executor_protocol") != stored.get(
        "protocol"
    ) or selected_manifest.get("executor_protocol") != stored.get("protocol"):
        raise ValueError("Development batches disagree with the frozen protocol")
    executor_args = _executor_args(config, args)
    for name, expected in (
        ("suite", config["suite"]),
        ("task_id", config["task_id"]),
        ("seed", config["seed"]),
        ("max_steps", config["max_steps"]),
        ("control", config["control"]),
    ):
        if baseline_manifest.get(name) != expected or selected_manifest.get(name) != expected:
            raise ValueError(f"Development {name} disagrees with the frozen executor configuration")
    for name in (
        "policy",
        "observation_mode",
        "simulator_sha256",
        "evaluator_sha256",
        "dependency_lock_sha256",
    ):
        if baseline_manifest.get(name) != selected_manifest.get(name):
            raise ValueError(f"Development conditions disagree: {name}")
    if baseline_manifest.get("policy") != "inspect-robots-agent":
        raise ValueError("Frozen evaluation requires the actual acting policy")
    same_task = (args.suite, args.task_id) == (config["suite"], config["task_id"])
    if same_task and DEVELOPMENT_STATES.intersection(args.states):
        raise ValueError("Same-task frozen evaluation cannot consume development states 0, 1, 2")
    if same_task and not set(args.states).issubset(range(3, 8)):
        raise ValueError("Same-task frozen evaluation accepts reserved states 3 through 7 only")
    current = loop.profile(executor_args)
    if current.get("identity") != stored.get("identity"):
        raise ValueError("Code or dependencies changed after development selection")
    if current.get("base_prompt") != stored.get("base_prompt"):
        raise ValueError("Executor base prompt changed after development selection")
    target_config = {**config, "suite": args.suite, "task_id": args.task_id}
    if current.get("executor_config") != target_config:
        raise ValueError("Current executor configuration differs beyond the requested task")
    expected_protocol = {
        **stored["protocol"],
        "executor_config_sha256": _json_hash(target_config),
    }
    if current.get("protocol") != expected_protocol:
        raise ValueError("Current executor protocol differs from the frozen protocol")

    output.mkdir(parents=True, exist_ok=False)
    report = {
        "mode": "frozen_evaluation",
        "status": "running",
        "split": "held_out",
        "source_loop": str(loop_dir),
        "suite": args.suite,
        "task_id": args.task_id,
        "state_ids": list(args.states),
        "control": config["control"],
        "conditions": {},
        "selection_performed": False,
        "improvement_invoked": False,
        "active_skills_updated": False,
    }
    loop.save(output / "frozen-evaluation.json", report)
    deadline = time.monotonic() + args.max_seconds
    try:
        baseline_input = _snapshot_skill(
            baseline_skill,
            output / "source-snapshots/baseline",
            baseline_manifest["skill_hash"],
        )
        selected_input = _snapshot_skill(
            selected_skill,
            output / "source-snapshots/selected",
            selected_manifest["skill_hash"],
        )
        baseline_run = loop.batch(
            executor_args,
            output / "baseline",
            baseline_input,
            current,
            deadline,
            states=args.states,
            split="held_out",
        )
        selected_run = loop.batch(
            executor_args,
            output / "selected",
            selected_input,
            current,
            deadline,
            states=args.states,
            split="held_out",
        )
        _validate_held_out_pair(baseline_run, selected_run, args.states)
        baseline_result = _condition(baseline_run, baseline)
        selected_result = _condition(selected_run, selected)
        report["conditions"] = {
            "baseline": baseline_result,
            "selected": selected_result,
        }
        report["comparison"] = {
            "baseline_successes": baseline_result["successes"],
            "selected_successes": selected_result["successes"],
            "success_delta": selected_result["successes"] - baseline_result["successes"],
        }
        report["status"] = "complete"
    except (TimeoutError, subprocess.TimeoutExpired):
        report["status"] = "time_limit"
    except Exception as exc:
        report.update(status="error", error_type=type(exc).__name__)
        raise
    finally:
        loop.save(output / "frozen-evaluation.json", report)
    print(
        f"Frozen evaluation: {report['status']}; artifacts: {output}",
        flush=True,
    )
    return report


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", type=Path, required=True, help="Completed development loop")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--states", type=int, nargs="+", required=True)
    parser.add_argument("--max-seconds", type=float, default=3600)
    args = parser.parse_args(argv)
    if (
        not args.states
        or len(set(args.states)) != len(args.states)
        or any(isinstance(state, bool) or state < 0 for state in args.states)
    ):
        parser.error("states must be distinct nonnegative integers")
    if args.task_id < 0:
        parser.error("task-id must be nonnegative")
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        parser.error("max-seconds must be positive and finite")
    return args


if __name__ == "__main__":
    run(arguments())
