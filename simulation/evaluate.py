"""Fixed-state evaluation; plug in Laksiya's run_episode(robot, skill_dir)."""

import argparse
import hashlib
import importlib
import json
import shutil
import time
from pathlib import Path

from simulation.sim import SUITES, Robot, positive_int


def skill_hash(directory):
    digest = hashlib.sha256()
    if directory is not None:
        for path in sorted(Path(directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
                digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def noop(robot, skill_dir):
    """Infrastructure baseline, not an intelligent policy or skill ablation."""
    while not robot.done:
        robot.step([0.0] * 6 + [-1.0], repeat=100)


def evaluate(
    policy,
    output,
    suite="libero_goal",
    task_id=0,
    state_ids=(0, 1, 2),
    seed=0,
    max_steps=500,
    skill_dir=None,
    privileged=False,
    label="noop",
):
    if (
        not state_ids
        or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in state_ids)
        or len(set(state_ids)) != len(state_ids)
    ):
        raise ValueError("state_ids must be nonempty, distinct, nonnegative integers")
    positive_int(max_steps, "max_steps")
    source = Path(skill_dir).resolve() if skill_dir is not None else None
    if source is not None and not source.is_dir():
        raise ValueError(f"Skill directory does not exist: {source}")
    output = Path(output).resolve()
    if source is not None and (output == source or source in output.parents):
        raise ValueError("Output must not be inside the skill directory")
    output.mkdir(parents=True, exist_ok=False)
    snapshot = None
    if source is not None:
        snapshot = output / "skills"
        shutil.copytree(source, snapshot, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    fingerprint = skill_hash(snapshot)
    manifest = dict(
        policy=label,
        suite=suite,
        task_id=task_id,
        state_ids=list(state_ids),
        seed=seed,
        max_steps=max_steps,
        skill_hash=fingerprint,
        observation_mode="privileged" if privileged else "rgb_proprio",
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    for state_id in state_ids:
        # Setup errors abort the run; they are not task failures.
        with Robot(
            suite=suite,
            task_id=task_id,
            init_state_id=state_id,
            seed=seed,
            max_steps=max_steps,
            output=output / f"state-{state_id:03d}",
            privileged=privileged,
        ) as robot:
            error = None
            try:
                policy(robot, snapshot)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            row = {**robot.result(), "policy": label, "skill_hash": fingerprint, "error": error}
            if error:
                row["success"] = False
                row["termination"] = "policy_error"
            # Changing the candidate while evaluating invalidates comparability.
            if skill_hash(snapshot) != fingerprint:
                raise RuntimeError("Policy modified the skill snapshot during evaluation")
            (robot.output / "result.json").write_text(json.dumps(row, indent=2) + "\n")
            rows.append(row)
            with (output / "results.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            print(f"state={state_id} success={row['success']} steps={row['steps']}", flush=True)
    summary = {
        **manifest,
        "episodes": len(rows),
        "successes": sum(row["success"] for row in rows),
        "success_rate": sum(row["success"] for row in rows) / len(rows),
        "policy_errors": sum(row["error"] is not None for row in rows),
        "mean_steps": sum(row["steps"] for row in rows) / len(rows),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default="noop", help="noop or module:function")
    parser.add_argument("--suite", choices=SUITES, default="libero_goal")
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--states", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--skills", type=Path)
    parser.add_argument("--privileged", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if args.policy == "noop":
        policy = noop
    else:
        module, function = args.policy.split(":", 1)
        policy = getattr(importlib.import_module(module), function)
    summary = evaluate(
        policy,
        args.output or f"runs/eval-{time.time_ns()}",
        suite=args.suite,
        task_id=args.task_id,
        state_ids=args.states,
        seed=args.seed,
        max_steps=args.max_steps,
        skill_dir=args.skills,
        privileged=args.privileged,
        label=args.policy,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
