"""Experimental stock Inspect Robots agent on LIBERO; separate from harness/."""

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
from importlib.metadata import version
from pathlib import Path

import httpx
import imageio.v2 as imageio
import numpy as np
from dotenv import dotenv_values
from inspect_robots import (
    ActionSemantics,
    Box,
    CameraSpec,
    EmbodimentInfo,
    Observation,
    ObservationSpace,
    Scene,
    StateField,
    StateSpec,
    StepResult,
    Task,
    episode_length,
    eval,
    success_at_end,
)
from inspect_robots_agent import LLMAgentPolicy

from evaluation.evaluate import skill_hash
from simulation.sim import SUITES, Robot, positive_int, vector

ROOT = Path(__file__).resolve().parents[1]
LIMITS = np.array([0.01, 0.01, 0.01, 1.0])


def image_horizon(model):
    # Fable 5.1 thinking signatures bind to the preceding conversation. Removing
    # old images would change that prefix and can invalidate subsequent requests.
    return None if model == "claude-fable-5-1" else 2


DOCS = """LIBERO Panda robot, XYZ translation and gripper only. Orientation is fixed.
move_by displacements dx, dy, dz are in WORLD METERS, not normalized controls.
Each emitted step commands at most 0.01 m per axis; actual motion can lag or be
blocked. Re-observe progress. Rotation is unavailable in this experiment.
grip is a command direction: positive CLOSES, negative OPENS, zero/omitted
retains the last command. It is not a distance. Closing for one step may not
finish the grasp; subsequent steps retain the command. Zero translation can
advance physics while the fingers move. Finger qpos is measured in meters;
a closed gripper does not establish contact or attachment. Images are upright
agentview and robot0_eye_in_hand cameras. Only LIBERO decides task success;
calling done cannot declare benchmark success. Reward is sparse success reward.
"""


class LiberoEmbodiment:
    """Translate Inspect's physical XYZ increments to the existing OSC robot API."""

    def __init__(self, output, suite="libero_goal", task_id=0, state_id=0, max_steps=300):
        self.output = Path(output)
        self.suite, self.task_id, self.state_id, self.max_steps = (
            suite,
            task_id,
            state_id,
            max_steps,
        )
        self.robot = None
        self.info = EmbodimentInfo(
            name="libero_xyz_gripper",
            is_simulated=True,
            control_hz=20,
            capabilities=frozenset({"seedable", "resettable", "privileged_success", "renderable"}),
            action_space=Box(
                shape=(4,),
                low=-LIMITS,
                high=LIMITS,
                semantics=ActionSemantics(
                    control_mode="eef_delta_pos",
                    rotation_repr="none",
                    gripper="binary",
                    frame="world",
                    dim_labels=("dx", "dy", "dz", "grip"),
                ),
            ),
            observation_space=ObservationSpace(
                cameras=tuple(
                    CameraSpec(name, 256, 256) for name in ("agentview", "robot0_eye_in_hand")
                ),
                state=StateSpec(
                    fields=(
                        StateField("eef_pos", (3,), "m"),
                        StateField("eef_quat", (4,), "unit_quat"),
                        StateField("finger_qpos", (2,), "m"),
                    )
                ),
            ),
            docs=DOCS,
        )

    def reset(self, scene, *, seed=None):
        if self.robot is not None:
            raise ValueError("This experiment owns one fresh episode per invocation")
        self.robot = Robot(
            suite=self.suite,
            task_id=self.task_id,
            init_state_id=self.state_id,
            seed=0 if seed is None else seed,
            max_steps=self.max_steps,
            output=self.output / f"state-{self.state_id:03d}",
        )
        return self.observation(self.robot.observe())

    def observation(self, raw):
        return Observation(
            images={name: imageio.imread(path) for name, path in raw["images"].items()},
            state={
                "eef_pos": np.array(raw["eef_pos"]),
                "eef_quat": np.array(raw["eef_quat_xyzw"]),
                "finger_qpos": np.array(raw["gripper_qpos"]),
            },
            instruction=raw["instruction"],
        )

    def step(self, action):
        values = vector(action.data, 4, "Inspect action")
        if np.any(np.abs(values) > LIMITS + 1e-9):
            raise ValueError("Inspect action exceeds declared per-step bounds")
        grip = self.robot.gripper_command if values[3] == 0 else float(np.sign(values[3]))
        raw = self.robot.step([*(values[:3] / 0.05), 0.0, 0.0, 0.0, grip])
        return StepResult(
            observation=self.observation(raw),
            reward=float(raw["success"]),
            terminated=raw["success"],
            termination_reason="success" if raw["success"] else None,
            truncated=raw["done"] and not raw["success"],
            info={"success": raw["success"]},
        )

    def close(self):
        if self.robot is not None:
            self.robot.close()


class RequestBudget(httpx.BaseTransport):
    """Bound all HTTP attempts (including plugin retries); save no headers or keys."""

    def __init__(self, output, maximum, deadline):
        self.inner = httpx.HTTPTransport(retries=0)
        self.output, self.maximum, self.deadline, self.count = Path(output), maximum, deadline, 0

    def handle_request(self, request):
        remaining = self.deadline - time.monotonic()
        if self.count >= self.maximum or remaining <= 0:
            raise RuntimeError("Experiment API request/time budget exhausted")
        self.count += 1
        request.extensions["timeout"] = {
            name: min(30.0, remaining) for name in ("connect", "read", "write", "pool")
        }
        started = time.monotonic()
        response = self.inner.handle_request(request)
        response.read()
        try:
            body = response.json()
        except ValueError:
            body = {}
        sent = json.loads(request.content)
        record = {
            "request": self.count,
            "requested_model": sent.get("model"),
            "requested_speed": sent.get("speed"),
            "http_status": response.status_code,
            "response_model": body.get("model"),
            "usage": body.get("usage"),
            "wall_seconds": time.monotonic() - started,
        }
        with (self.output / "requests.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        return response

    def close(self):
        self.inner.close()


def run(args):
    output = (args.output or ROOT / "runs" / f"inspect-agent-{time.time_ns()}").resolve()
    key = os.environ.get("CLAUDE_API_KEY") or dotenv_values(ROOT / ".env").get("CLAUDE_API_KEY")
    if not key and not args.smoke:
        raise ValueError("CLAUDE_API_KEY is required in .env or the environment")
    output.mkdir(parents=True, exist_ok=False)
    skill = None
    if args.skill:
        skill = output / "skills" / "task" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        shutil.copyfile(args.skill, skill)
    config = {
        "experiment": "inspect-robots-agent",
        "model": args.model,
        "wire": "messages",
        "speed": args.speed,
        "image_horizon": image_horizon(args.model),
        "effort": args.effort,
        "max_api_requests": args.max_calls,
        "max_steps": args.max_steps,
        "timeout_seconds": args.timeout,
        "state_id": args.state,
        "suite": args.suite,
        "task_id": args.task_id,
        "seed": args.seed,
        "rotation": "fixed",
        "skill_hash": skill_hash(output / "skills" if skill else None),
        "packages": {
            name: version(name) for name in ("inspect-robots", "inspect-robots-agent", "mujoco")
        },
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "smoke": args.smoke,
    }
    (output / "experiment.json").write_text(json.dumps(config, indent=2) + "\n")
    embodiment = LiberoEmbodiment(output, args.suite, args.task_id, args.state, args.max_steps)
    transport = RequestBudget(output, args.max_calls, time.monotonic() + args.timeout)
    policy = None
    error = None
    log = None
    old_handler = signal.getsignal(signal.SIGALRM)

    def timeout_handler(*_):
        raise KeyboardInterrupt("Experiment wall-time budget exhausted")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        scene = Scene(
            id=f"state-{args.state:03d}",
            instruction="Goal supplied by LIBERO observation",
            init_seed=args.seed,
        )
        if args.smoke:
            before = embodiment.reset(scene, seed=args.seed)
            from inspect_robots import Action

            for _ in range(min(args.max_steps, 10)):
                embodiment.step(Action(np.array([0.0, 0.0, 0.002, -1.0])))
            displacement = np.linalg.norm(
                embodiment.robot.observe()["eef_pos"] - before.state["eef_pos"]
            )
            print(f"Smoke displacement: {displacement:.6f} m")
        else:
            # Resolve the instruction without creating a second simulator.
            from simulation.sim import load_suite

            instruction = load_suite(args.suite).get_task(args.task_id).language
            scene = Scene(id=scene.id, instruction=instruction, init_seed=args.seed)
            policy = LLMAgentPolicy(
                model=args.model,
                wire="messages",
                speed="fast" if args.speed == "fast" else None,
                effort=args.effort,
                max_output_tokens=1024,
                max_llm_calls=args.max_calls,
                images="always",
                depth="off",
                image_horizon=image_horizon(args.model),
                prior_learnings=str(skill) if skill else None,
                base_url="https://api.anthropic.com/v1",
                api_key_env="CLAUDE_API_KEY",
                env={"CLAUDE_API_KEY": key},
                transport=transport,
                wire_capture=False,
            )
            task = Task(
                name=f"{args.suite}-task-{args.task_id}",
                scenes=[scene],
                scorer=[success_at_end(), episode_length()],
                max_steps=args.max_steps,
            )
            (log,) = eval(
                task,
                policy,
                embodiment,
                log_dir=str(output / "inspect"),
                seed=args.seed,
                store_frames=True,
                fail_on_error=True,
            )
            if log.status == "error":
                error = (
                    next((sample.error for sample in log.samples if sample.error), log.error)
                    or "Inspect evaluation failed"
                )
    except (Exception, KeyboardInterrupt) as exc:
        error = f"{type(exc).__name__}: {exc}".replace(key or "NO_KEY", "[REDACTED]")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if policy is not None:
            (output / "executor-trace.json").write_text(
                json.dumps(policy.transcript(), indent=2) + "\n"
            )
        if embodiment.robot is not None:
            result = {
                **embodiment.robot.result(),
                "policy": "noop" if args.smoke else "inspect-robots-agent",
                "skill_hash": config["skill_hash"],
                "error": error,
                "requested_model": args.model,
                "requested_speed": args.speed,
                "api_requests_attempted": transport.count,
                "rotation": "fixed",
            }
            if error:
                result.update(success=False, termination="policy_error")
            (embodiment.robot.output / "result.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
        embodiment.close()
        transport.close()
    if error:
        (output / "error.json").write_text(json.dumps({"error": error}) + "\n")
    if log is not None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from inspect_robots.cli import main; raise SystemExit(main())",
                "view",
                str(output / "inspect"),
            ],
            check=True,
        )
    print(f"Artifacts: {output}")
    if embodiment.robot is not None:
        print(f"LIBERO success: {result['success']}; steps: {embodiment.robot.steps}")
    if error:
        raise SystemExit(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--suite", choices=SUITES, default="libero_goal")
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--state", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--speed", choices=["fast", "standard"], default="fast")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--skill", type=Path, help="Optional Markdown notes, frozen before the episode"
    )
    parser.add_argument("--smoke", action="store_true", help="Test the adapter without an API call")
    args = parser.parse_args()
    positive_int(args.max_calls, "max_calls")
    positive_int(args.max_steps, "max_steps")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    run(args)


if __name__ == "__main__":
    main()
