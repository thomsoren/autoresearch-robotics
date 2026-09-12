"""Fixed-state evaluation; plug in Laksiya's run_episode(robot, skill_dir)."""

import argparse
import base64
import hashlib
import html
import importlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
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


def validate_protocol(protocol):
    """Validate declared executor settings; the harness must enforce these."""
    keys = {
        "model",
        "base_prompt_sha256",
        "executor_revision",
        "executor_config_sha256",
        "max_turns",
        "max_budget_usd",
        "timeout_seconds",
        "fresh_session_per_episode",
    }
    if not isinstance(protocol, dict) or set(protocol) not in (keys, keys | {"max_api_requests"}):
        raise ValueError(f"Protocol requires exactly: {', '.join(sorted(keys))}")
    for name in ("model", "executor_revision"):
        if not isinstance(protocol[name], str) or not protocol[name].strip():
            raise ValueError(f"Protocol {name} must be a nonempty string")
    for name in ("base_prompt_sha256", "executor_config_sha256"):
        if not isinstance(protocol[name], str) or not re.fullmatch(r"[a-f0-9]{64}", protocol[name]):
            raise ValueError(f"Protocol {name} must be a SHA-256 hex digest")
    positive_int(protocol["max_turns"], "max_turns")
    if "max_api_requests" in protocol:
        positive_int(protocol["max_api_requests"], "max_api_requests")
    for name in ("max_budget_usd", "timeout_seconds"):
        value = protocol[name]
        if name == "max_budget_usd" and value is None and "max_api_requests" in protocol:
            continue  # Request-limited executor; no dollar cap is claimed.
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Protocol {name} must be positive and finite")
    if protocol["fresh_session_per_episode"] is not True:
        raise ValueError("Protocol requires a fresh executor session for every episode")


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
    protocol=None,
):
    if protocol is not None:
        validate_protocol(protocol)
        protocol = json.loads(json.dumps(protocol))
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
        executor_protocol=protocol,
        simulator_sha256=hashlib.sha256(
            Path(__file__).parents[1].joinpath("simulation/sim.py").read_bytes()
        ).hexdigest(),
        evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        dependency_lock_sha256=hashlib.sha256(
            Path(__file__).parents[1].joinpath("uv.lock").read_bytes()
        ).hexdigest(),
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


def load_batch(run):
    """Read complete, internally consistent results and verify the skill snapshot."""
    run = Path(run).resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    summary = json.loads((run / "summary.json").read_text())
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    states = manifest["state_ids"]
    if "episode_seeds" in manifest:
        seeds = manifest["episode_seeds"]
        if (
            not isinstance(seeds, dict)
            or set(seeds) != {str(state) for state in states}
            or any(type(value) is not int for value in seeds.values())
        ):
            raise ValueError("Derived episode seeds must cover every declared state")
    if (
        not rows
        or [row["init_state_id"] for row in rows] != states
        or len(set(states)) != len(states)
    ):
        raise ValueError("Requires a complete batch with the declared states in order")
    for row in rows:
        if type(row["success"]) is not bool or (row.get("error") is not None and row["success"]):
            raise ValueError("Every episode needs boolean success; policy errors must score false")
        if type(row["steps"]) is not int or row["steps"] < 0:
            raise ValueError("Episode steps must be a nonnegative integer")
        if any(row.get(key) != manifest[key] for key in ("policy", "skill_hash")):
            raise ValueError("Episode policy/skill hash differs from the batch manifest")
        path = run / f"state-{row['init_state_id']:03d}" / "result.json"
        if json.loads(path.read_text()) != row:
            raise ValueError("Episode result differs from the batch results")
        if "initial_frame_sha256" in manifest:
            frames = manifest["initial_frame_sha256"].get(str(row["init_state_id"]), {})
            if set(frames) != {"agentview", "robot0_eye_in_hand"} or any(
                hashlib.sha256((path.parent / f"0000-{camera}.png").read_bytes()).hexdigest()
                != expected
                for camera, expected in frames.items()
            ):
                raise ValueError("Initial camera evidence is missing or changed")
    expected = {
        "episodes": len(rows),
        "successes": sum(row["success"] for row in rows),
        "success_rate": sum(row["success"] for row in rows) / len(rows),
        "policy_errors": sum(row.get("error") is not None for row in rows),
        "mean_steps": sum(row["steps"] for row in rows) / len(rows),
    }
    if any(summary.get(key) != value for key, value in {**manifest, **expected}.items()):
        raise ValueError("Stored summary disagrees with the manifest/results; refusing to rescore")
    snapshot = run / "skills"
    if skill_hash(snapshot if snapshot.is_dir() else None) != manifest["skill_hash"]:
        raise ValueError("Skill snapshot is missing or changed since evaluation")
    return manifest, summary, rows


def compare_runs(baseline, candidate, output=None):
    """Select a frozen development snapshot; never edit active skills or run policies."""
    baseline, candidate = Path(baseline).resolve(), Path(candidate).resolve()
    old, old_summary, old_rows = load_batch(baseline)
    new, new_summary, new_rows = load_batch(candidate)
    if old["state_ids"] != [0, 1, 2] or new["state_ids"] != [0, 1, 2]:
        raise ValueError(
            "Skill selection requires development states [0, 1, 2]; held-out states cannot select skills"
        )
    reasons = []
    for key in ("policy", "suite", "task_id", "seed", "max_steps", "observation_mode"):
        if old[key] != new[key]:
            reasons.append(f"Different {key}")
    if old.get("episode_seeds") != new.get("episode_seeds"):
        reasons.append("Different episode_seeds")
    if old.get("initial_frame_sha256") != new.get("initial_frame_sha256"):
        reasons.append("Different initial camera frames")
    for key in (
        "executor_protocol",
        "simulator_sha256",
        "evaluator_sha256",
        "dependency_lock_sha256",
    ):
        if not old.get(key) or not new.get(key):
            reasons.append(f"Missing {key}; provenance cannot be established retroactively")
        elif old[key] != new[key]:
            reasons.append(f"Different {key}")
    for manifest in (old, new):
        if manifest.get("executor_protocol"):
            validate_protocol(manifest["executor_protocol"])
    if "noop" in (old["policy"], new["policy"]):
        reasons.append("No-op runs are infrastructure fixtures, not skill evaluations")
    for run_manifest, rows in ((old, old_rows), (new, new_rows)):
        for row in rows:
            for key in ("suite", "task_id", "seed", "max_steps", "observation_mode"):
                expected = run_manifest[key]
                if key == "seed" and "episode_seeds" in run_manifest:
                    expected = run_manifest["episode_seeds"].get(str(row["init_state_id"]))
                if expected is None or row.get(key) != expected:
                    reasons.append(
                        f"Episode state {row['init_state_id']} has missing/mismatched {key}"
                    )
    for left, right in zip(old_rows, new_rows, strict=True):
        for key in ("libero_revision", "settle_steps", "task_name"):
            if left.get(key) is None or right.get(key) is None or left[key] != right[key]:
                reasons.append(
                    f"Episode state {left['init_state_id']} has missing/mismatched {key}"
                )
    candidate_files = [p for p in (candidate / "skills").rglob("*") if p.is_file()]
    if not candidate_files or any(p.suffix != ".md" for p in candidate_files):
        reasons.append("Candidate must be a nonempty Markdown-only skill snapshot")
    delta = new_summary["successes"] - old_summary["successes"]
    if reasons:
        decision = "inconclusive"
    elif old["skill_hash"] == new["skill_hash"]:
        decision, reasons = "reject", ["Skill content did not change"]
    elif delta > 0:
        decision, reasons = "keep", ["Development success count strictly improved"]
    else:
        decision, reasons = (
            "reject",
            ["Tie: retain incumbent" if delta == 0 else "Success count regressed"],
        )
    selected = candidate if decision == "keep" else baseline
    report = {
        "decision": decision,
        "reasons": list(dict.fromkeys(reasons)),
        "split": "development",
        "baseline": str(baseline),
        "candidate": str(candidate),
        "baseline_successes": old_summary["successes"],
        "candidate_successes": new_summary["successes"],
        "attempts_per_condition": 3,
        "success_delta": delta,
        "baseline_policy_errors": old_summary["policy_errors"],
        "candidate_policy_errors": new_summary["policy_errors"],
        "baseline_skill_hash": old["skill_hash"],
        "candidate_skill_hash": new["skill_hash"],
        "selected_skill_dir": str(selected / "skills") if (selected / "skills").is_dir() else None,
        "active_skills_updated": False,
        "limitations": [
            "Executor protocol is declared metadata; the harness must enforce fresh sessions and API budgets.",
            "One development comparison does not establish held-out generalization.",
        ],
        "source_sha256": {
            label: {
                name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                for name in ("manifest.json", "results.jsonl", "summary.json")
            }
            for label, run in (("baseline", baseline), ("candidate", candidate))
        },
    }
    output = Path(output).resolve() if output else candidate / "comparison.json"
    if any(output.is_relative_to(run / "skills") for run in (baseline, candidate)):
        raise ValueError("Comparison output must not be inside a skill snapshot")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    return report


def export_report(run):
    """Render a completed batch with Inspect Robots; never execute or rescore it."""
    from inspect_robots import EvalLog, EvalResults, EvalSpec, EvalStats, SceneResult

    run = Path(run).resolve()
    manifest, summary, rows = load_batch(run)

    output = run / "inspect"
    output.mkdir(exist_ok=False)
    samples = []
    for row in rows:
        scene = f"state-{row['init_state_id']:03d}"
        episode = run / scene
        scores = {"libero_success": float(row["success"]), "episode_length": float(row["steps"])}
        samples.append(
            SceneResult(
                scene_id=scene,
                status="error" if row.get("error") is not None else "success",
                instruction=row.get("instruction"),
                error=row.get("error"),
                reduced=scores,
                epochs=(scores,),
                termination_reasons=(row.get("termination"),),
                scene_metadata={
                    "init_state_id": row["init_state_id"],
                    "seed": row.get("seed"),
                    "fixture": manifest["policy"] == "noop",
                },
                trial_metadata=(
                    {
                        "source_episode": str(episode),
                        "video": str(episode / "episode.mp4"),
                        "actions": str(episode / "actions.jsonl"),
                        "missing_evidence": "Executor transcript not imported; low-level actions are not tool calls.",
                    },
                ),
                policy_transcripts=(None,),
            )
        )
    log = EvalLog(
        version=EvalLog.SCHEMA_VERSION,
        status="error" if summary["policy_errors"] else "success",
        eval=EvalSpec(
            task=rows[0].get("task_name", f"{manifest['suite']}/task-{manifest['task_id']}"),
            policy=manifest["policy"],
            embodiment="libero-export",
            created=datetime.now(timezone.utc).isoformat(),
            inspect_robots_version=version("inspect-robots"),
            seed=manifest["seed"],
            max_steps=manifest["max_steps"],
            policy_config={
                **manifest,
                "report_only": True,
                "scoring": "Stored LIBERO success; all attempts, including policy/API errors, are in the denominator.",
                "source_run": str(run),
                "source_sha256": {
                    name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                    for name in ("manifest.json", "results.jsonl", "summary.json")
                },
            },
            embodiment_info={
                "libero_revision": rows[0].get("libero_revision"),
                "media": "Original videos and saved stills are embedded in the HTML appendix, within a 20 MB source-media budget.",
                "timing": "Sum of recorded episode wall_seconds; unavailable values contribute zero. Start/end timestamps not recorded.",
            },
        ),
        results=EvalResults(
            total_scenes=len(rows),
            total_trials=len(rows),
            errored_trials=summary["policy_errors"],
            metrics={
                "libero_success": summary["success_rate"],
                "successes": summary["successes"],
                "attempts": summary["episodes"],
                "policy_errors": summary["policy_errors"],
                "episode_length": summary["mean_steps"],
            },
        ),
        stats=EvalStats(
            started_at="unknown (imported)",
            completed_at="unknown (imported)",
            duration_s=sum(row.get("wall_seconds", 0) for row in rows),
            total_steps=sum(row["steps"] for row in rows),
        ),
        samples=tuple(samples),
    )
    log_path = output / "log.json"
    log_path.write_text(json.dumps(log.to_dict(), indent=2, allow_nan=False) + "\n")
    # Use the supported CLI, not private HTML helpers. It displays images only
    # alongside transcripts; add a clearly separate gallery for our raw exports.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from inspect_robots.cli import main; raise SystemExit(main())",
            "view",
            str(log_path),
            "--no-video",
        ],
        check=True,
        cwd=output,
    )
    report = output / "log.html"
    gallery = [
        '<section class="scene"><h2>Imported episode media</h2>',
        "<p>Original simulator videos and saved stills. Executor transcripts are unavailable; "
        "these images do not establish which frames an agent saw.</p>",
    ]
    remaining = 20_000_000
    for row in rows:
        scene = f"state-{row['init_state_id']:03d}"
        gallery.append(f"<h3>{scene}</h3>")
        for path in sorted((run / scene).iterdir()):
            if path.name != "episode.mp4" and not re.fullmatch(
                r"\d+-(agentview|robot0_eye_in_hand)\.png", path.name
            ):
                continue
            caption = html.escape(f"{scene}/{path.name}")
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > remaining:
                gallery.append(
                    f"<p>{caption}: omitted from report (media budget); available in source episode.</p>"
                )
                continue
            data = path.read_bytes()
            remaining -= len(data)
            payload = base64.b64encode(data).decode()
            if path.suffix == ".mp4":
                media = f'<video controls muted preload="metadata" style="max-width:100%" src="data:video/mp4;base64,{payload}"></video>'
            else:
                media = f'<img loading="lazy" style="max-width:100%" alt="{caption}" src="data:image/png;base64,{payload}">'
            gallery.append(f"<figure>{media}<figcaption>{caption}</figcaption></figure>")
    gallery.append("</section>")
    report.write_text(report.read_text().replace("</main>", "\n".join(gallery) + "</main>"))
    return report


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
    parser.add_argument(
        "--protocol", type=Path, help="Frozen executor protocol JSON, recorded before evaluation"
    )
    reporting = parser.add_mutually_exclusive_group()
    reporting.add_argument(
        "--report", action="store_true", help="Render an Inspect Robots report after evaluation"
    )
    reporting.add_argument(
        "--report-existing",
        type=Path,
        metavar="RUN_DIR",
        help="Report a completed batch without running the executor",
    )
    reporting.add_argument(
        "--compare",
        type=Path,
        nargs=2,
        metavar=("BASELINE", "CANDIDATE"),
        help="Compare completed development runs; --output selects comparison JSON",
    )
    args = parser.parse_args()
    if args.compare:
        if args.protocol:
            parser.error(
                "Protocol must be recorded before evaluation, not supplied at comparison time"
            )
        print(json.dumps(compare_runs(*args.compare, output=args.output), indent=2))
        return
    if args.report_existing:
        print(f"Report: {export_report(args.report_existing)}")
        return
    if args.policy == "noop":
        policy = noop
    else:
        module, function = args.policy.split(":", 1)
        policy = getattr(importlib.import_module(module), function)
    output = args.output or f"runs/eval-{time.time_ns()}"
    summary = evaluate(
        policy,
        output,
        suite=args.suite,
        task_id=args.task_id,
        state_ids=args.states,
        seed=args.seed,
        max_steps=args.max_steps,
        skill_dir=args.skills,
        privileged=args.privileged,
        label=args.policy,
        protocol=json.loads(args.protocol.read_text()) if args.protocol else None,
    )
    print(json.dumps(summary, indent=2))
    if args.report:
        print(f"Report: {export_report(output)}")


if __name__ == "__main__":
    main()
