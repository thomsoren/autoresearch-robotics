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
from scipy.spatial.transform import Rotation

from evaluation.evaluate import skill_hash
from execute.control import pose_precheck, rotation_from_6d, servo_pose
from execute.operational_policy import OperationalPolicy
from execute.reactive_controller import ReactiveController
from simulation.sim import SUITES, Robot, positive_int, vector

ROOT = Path(__file__).resolve().parents[1]
LIMITS = np.array([0.01, 0.01, 0.01, 1.0])


def image_horizon(model):
    # Fable 5.1 thinking signatures bind to the preceding conversation. Removing
    # old images would change that prefix and can invalidate subsequent requests.
    return None if model == "claude-fable-5-1" else 2


def output_token_limit(model):
    # Fable's response budget also covers thinking before its robot tool call.
    return 8192 if model == "claude-fable-5-1" else 1024


DOCS = """LIBERO Panda robot. Images are upright external and wrist RGB views.
Each displayed image is 256 by 256 pixels. locate_pixels queries calibrated
depth for chosen visible pixels without moving the robot. It reports WORLD
surface positions in meters, not object centers or verified grasp targets.
The queried camera geometry belongs to the current observation, including wrist motion.
For note and hindsight, provide only a brief operational summary: visible scene
evidence, the intended action, or the observed outcome and practical lesson.
Position and orientation are measured at the same grip site in WORLD coordinates.
The feedback controller attempts each waypoint with bounded OSC commands; it does
not plan around obstacles or guarantee arrival. Each Inspect waypoint can consume
up to 30 PHYSICS steps. Native Inspect step counts are waypoints, not physics time.
Read physics_steps, remaining_steps, motion_position_error, motion_rotation_error
and motion_reached after a call. Re-observe and adapt when movement falls short.
If a waypoint stalls or reaches its motion limit, its remaining queued waypoints
are discarded and you receive a fresh observation. Choose the next action from
the measured pose and visible scene; the previous destination was not reached.
Finger qpos is measured in meters. Closed fingers do not establish a grasp.
Only LIBERO decides success; done cannot declare it. Reward is sparse success.
"""
POSE_DOCS = """
move_to accepts absolute targets x,y,z in meters plus orientation axes and grip.
Orientation is rot6d: xx,xy,xz are the tool's local X unit axis in world coordinates;
yx,yy,yz are its local Y unit axis. Local Z = X cross Y points toward the fingertips.
Supply both perpendicular unit axes together when changing orientation. Omitted
components hold the observed value. The controller orthonormalizes axes; parallel
or zero axes are rejected. For large rotations use intermediate orientations.
These six components are unitless direction vectors, NOT Euler angles or radians.
grip=0 commands OPEN; grip=1 commands CLOSE. Omission retains the last command.
The last eef_pose component is the commanded grip, not measured finger width.
A grip-only move_to keeps the measured pose and advances fingers for bounded time.
"""
XYZ_DOCS = """
move_by accepts total WORLD displacement dx,dy,dz in meters. Wrist stays fixed.
Positive grip CLOSES, negative OPENS, zero/omitted retains the command. A zero
translation still advances physics while fingers move. Orientation is unavailable.
"""
POSE_LOW = np.array([-1.0, -1.0, 0.3, *([-1.0] * 6), 0.0])
POSE_HIGH = np.array([1.0, 1.0, 1.8, *([1.0] * 6), 1.0])


class LiberoEmbodiment:
    """Translate Inspect's physical XYZ increments to the existing OSC robot API."""

    def __init__(
        self, output, suite="libero_goal", task_id=0, state_id=0, max_steps=300, control="pose"
    ):
        if control not in ("pose", "xyz"):
            raise ValueError("control must be pose or xyz")
        self.control = control
        self.last_motion = None
        self.output = Path(output)
        self.suite, self.task_id, self.state_id, self.max_steps = (
            suite,
            task_id,
            state_id,
            max_steps,
        )
        self.robot = None
        self.info = EmbodimentInfo(
            name=f"libero_{control}_gripper",
            is_simulated=True,
            control_hz=20,
            capabilities=frozenset({"seedable", "resettable", "privileged_success", "renderable"}),
            action_space=Box(
                shape=(10,) if control == "pose" else (4,),
                low=POSE_LOW if control == "pose" else -LIMITS,
                high=POSE_HIGH if control == "pose" else LIMITS,
                semantics=ActionSemantics(
                    control_mode="eef_abs_pose" if control == "pose" else "eef_delta_pos",
                    rotation_repr="rot6d" if control == "pose" else "none",
                    gripper="binary",
                    frame="world",
                    dim_labels=("x", "y", "z", "xx", "xy", "xz", "yx", "yy", "yz", "grip")
                    if control == "pose"
                    else ("dx", "dy", "dz", "grip"),
                    max_step=(0.01, 0.01, 0.01, *([0.05] * 6), 1.0) if control == "pose" else None,
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
                        StateField("eef_pose", (10,), "mixed"),
                        StateField("physics_steps", (1,), "steps"),
                        StateField("remaining_steps", (1,), "steps"),
                        StateField("motion_position_error", (1,), "m"),
                        StateField("motion_rotation_error", (1,), "rad"),
                        StateField("motion_reached", (1,), "bool"),
                    )
                ),
            ),
            docs=DOCS + (POSE_DOCS if control == "pose" else XYZ_DOCS),
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
            calibrated_depth=True,
        )
        return self.observation(self.robot.observe())

    def observation(self, raw):
        rotation = Rotation.from_quat(raw["eef_quat_xyzw"]).as_matrix()
        motion = self.last_motion or {}
        return Observation(
            images={name: imageio.imread(path) for name, path in raw["images"].items()},
            state={
                "eef_pos": np.array(raw["eef_pos"]),
                "eef_quat": np.array(raw["eef_quat_xyzw"]),
                "finger_qpos": np.array(raw["gripper_qpos"]),
                "eef_pose": np.r_[
                    raw["eef_pos"],
                    rotation[:, 0],
                    rotation[:, 1],
                    float(self.robot.gripper_command > 0),
                ],
                "physics_steps": np.array([raw["steps"]]),
                "remaining_steps": np.array([raw["remaining_steps"]]),
                "motion_position_error": np.array([motion.get("position_error", 0.0)]),
                "motion_rotation_error": np.array([motion.get("rotation_error", 0.0)]),
                "motion_reached": np.array([float(motion.get("reached", True))]),
            },
            instruction=raw["instruction"],
            extra={
                "discard_action_chunk": motion.get("stop_reason") in {"stalled", "motion_limit"},
                "camera_geometry": raw.get("camera_geometry", {}),
            },
        )

    def step(self, action):
        values = vector(action.data, 10 if self.control == "pose" else 4, "Inspect action")
        space = self.info.action_space
        if np.any(values < space.low - 1e-9) or np.any(values > space.high + 1e-9):
            raise ValueError("Inspect action exceeds declared bounds")
        if action.meta.get("request_stop"):
            raw = self.robot.observe()
            return StepResult(
                observation=self.observation(raw),
                reward=float(raw["success"]),
                terminated=raw["success"],
                truncated=raw["done"] and not raw["success"],
                info={"success": raw["success"]},
            )
        position, rotation = self.robot.pose()
        if self.control == "pose":
            target, rotation = values[:3], rotation_from_6d(values[3:9])
            grip = 1.0 if values[9] >= 0.5 else -1.0
        else:
            target = position + values[:3]
            grip = self.robot.gripper_command if values[3] == 0 else float(np.sign(values[3]))
        grip_changed = grip != self.robot.gripper_command
        raw = servo_pose(
            self.robot,
            target,
            rotation,
            grip=grip,
            min_steps=15 if grip_changed else 1,
            chunk_final=bool(action.meta.get("chunk_final", False)),
        )
        self.last_motion = raw
        return StepResult(
            observation=self.observation(raw),
            reward=float(raw["success"]),
            terminated=raw["success"],
            termination_reason="success" if raw["success"] else None,
            truncated=raw["done"] and not raw["success"],
            info={
                "success": raw["success"],
                "start_step": raw["start_step"],
                "end_step": raw["end_step"],
                "stop_reason": raw["stop_reason"],
            },
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
        request.extensions["timeout"]["read"] = min(120.0, remaining)
        started = time.monotonic()
        sent = json.loads(request.content)
        record = {
            "request": self.count,
            "requested_model": sent.get("model"),
            "requested_speed": sent.get("speed"),
            "http_status": None,
        }
        try:
            response = self.inner.handle_request(request)
            record["http_status"] = response.status_code
            response.read()
            try:
                body = response.json()
            except ValueError:
                body = {}
            record.update(
                response_model=body.get("model"),
                usage=body.get("usage"),
                stop_reason=body.get("stop_reason"),
            )
            if body.get("stop_reason") == "refusal":
                record["public_text"] = [
                    block["text"] for block in body.get("content", [])
                    if block.get("type") == "text" and isinstance(block.get("text"), str)
                ]
            return response
        except BaseException as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["wall_seconds"] = time.monotonic() - started
            with (self.output / "requests.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")

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
        "rotation": "rot6d" if args.control == "pose" else "fixed",
        "control": args.control,
        "skill_hash": skill_hash(output / "skills" if skill else None),
        "packages": {
            name: version(name) for name in ("inspect-robots", "inspect-robots-agent", "mujoco")
        },
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "smoke": args.smoke,
    }
    (output / "experiment.json").write_text(json.dumps(config, indent=2) + "\n")
    embodiment = LiberoEmbodiment(
        output, args.suite, args.task_id, args.state, args.max_steps, control=args.control
    )
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
                if embodiment.robot.done:
                    break
                if args.control == "pose":
                    action = (
                        embodiment.observation(embodiment.robot.observe()).state["eef_pose"].copy()
                    )
                    action[2] += 0.002
                else:
                    action = np.array([0.0, 0.0, 0.002, -1.0])
                embodiment.step(Action(action))
            displacement = np.linalg.norm(
                embodiment.robot.observe()["eef_pos"] - before.state["eef_pos"]
            )
            print(f"Smoke displacement: {displacement:.6f} m")
        else:
            # Resolve the instruction without creating a second simulator.
            from simulation.sim import load_suite

            instruction = load_suite(args.suite).get_task(args.task_id).language
            scene = Scene(id=scene.id, instruction=instruction, init_seed=args.seed)
            policy = OperationalPolicy(
                model=args.model,
                wire="messages",
                speed="fast" if args.speed == "fast" else None,
                effort=args.effort,
                max_output_tokens=output_token_limit(args.model),
                max_llm_calls=args.max_calls,
                images="always",
                depth="off",
                image_horizon=image_horizon(args.model),
                prior_learnings=str(skill) if skill else None,
                base_url="https://api.anthropic.com/v1",
                api_key_env="CLAUDE_API_KEY",
                env={"CLAUDE_API_KEY": key},
                transport=transport,
                pre_check=pose_precheck if args.control == "pose" else None,
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
                controller=ReactiveController(),
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
                "rotation": "rot6d" if args.control == "pose" else "fixed",
                "control": args.control,
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
    parser.add_argument("--control", choices=["pose", "xyz"], default="pose")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default="claude-fable-5-1")
    parser.add_argument("--speed", choices=["fast", "standard"], default="standard")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-steps", type=int, default=900)
    parser.add_argument("--timeout", type=float, default=300)
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
