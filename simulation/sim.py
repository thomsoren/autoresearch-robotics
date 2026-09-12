"""LIBERO adapter and smoke test. Keep one Robot instance alive per episode."""

import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np

from simulation.prepare import LIBERO_REV, configure

SUITES = ("libero_spatial", "libero_object", "libero_goal", "libero_90", "libero_10")


def load_suite(name):
    if name not in SUITES:
        raise ValueError(f"Unknown suite {name}; choose from {SUITES}")
    configure()
    os.environ.setdefault("MUJOCO_GL", "cgl" if platform.system() == "Darwin" else "egl")
    from libero.libero import benchmark

    return benchmark.get_benchmark_dict()[name](task_order_index=0)


def vector(value, size, label):
    array = np.asarray(value, dtype=float)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"{label} must contain {size} finite numbers")
    return array


def positive_int(value, label, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return value


class Robot:
    """Generic motion tools; success comes only from LIBERO's task predicate.

    `observe`, `step`, `move_to`, and `gripper` return JSON-compatible dictionaries.
    Camera paths point to PNGs that the SDK adapter should send as image content.
    All calls must run serially on the same thread (OpenGL context ownership).
    """

    def __init__(
        self,
        suite="libero_goal",
        task_id=0,
        init_state_id=0,
        seed=0,
        max_steps=500,
        output="runs/smoke",
        privileged=False,
        video=True,
    ):
        positive_int(max_steps, "max_steps")
        task_suite = load_suite(suite)
        if not 0 <= task_id < task_suite.n_tasks:
            raise ValueError(f"task_id must be in [0, {task_suite.n_tasks})")
        task = task_suite.get_task(task_id)
        states = task_suite.get_task_init_states(task_id)
        if not 0 <= init_state_id < len(states):
            raise ValueError(f"init_state_id must be in [0, {len(states)})")
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.max_steps, self.steps = max_steps, 0
        self.privileged = privileged
        self.success = False
        self.gripper_command = -1.0
        self._writer = None
        self._env = None
        self._started = time.monotonic()
        self.metadata = dict(
            suite=suite,
            task_id=task_id,
            task_name=task.name,
            instruction=task.language,
            init_state_id=init_state_id,
            seed=seed,
            max_steps=max_steps,
            settle_steps=10,
            observation_mode="privileged" if privileged else "rgb_proprio",
            libero_revision=LIBERO_REV,
            platform=platform.platform(),
        )
        try:
            from libero.libero.envs import OffScreenRenderEnv
            from robosuite.utils.errors import RandomizationError

            self._env = OffScreenRenderEnv(
                bddl_file_name=task_suite.get_task_bddl_file_path(task_id),
                camera_heights=256,
                camera_widths=256,
                control_freq=20,
                horizon=max_steps + 10,
                ignore_done=True,
            )
            self._env.seed(seed)
            # Avoid upstream ControlEnv.reset's unbounded retry loop.
            for attempt in range(5):
                try:
                    self._env.env.reset()
                    break
                except RandomizationError:
                    if attempt == 4:
                        raise
            self._obs = self._env.set_init_state(states[init_state_id])
            for _ in range(10):
                self._obs, _, _, _ = self._env.step([0.0] * 6 + [-1.0])
            self.success = bool(self._env.check_success())
            if video:
                import imageio.v2 as imageio

                self._writer = imageio.get_writer(str(self.output / "episode.mp4"), fps=4)
                self._record_frame()
            (self.output / "episode.json").write_text(json.dumps(self.metadata, indent=2) + "\n")
        except BaseException:
            self.close()
            raise

    @property
    def done(self):
        return self.success or self.steps >= self.max_steps

    def _image(self, camera):
        # robosuite 1.4 defaults to OpenGL's bottom-up image convention.
        return np.ascontiguousarray(self._obs[f"{camera}_image"][::-1])

    def _record_frame(self):
        if self._writer is not None:
            self._writer.append_data(self._image("agentview"))

    def observe(self):
        import imageio.v2 as imageio

        images = {}
        for camera in ("agentview", "robot0_eye_in_hand"):
            path = self.output / f"{self.steps:04d}-{camera}.png"
            imageio.imwrite(path, self._image(camera))
            images[camera] = str(path)
        result = dict(
            instruction=self.metadata["instruction"],
            images=images,
            eef_pos=self._obs["robot0_eef_pos"].tolist(),
            eef_quat_xyzw=self._obs["robot0_eef_quat"].tolist(),
            gripper_qpos=self._obs["robot0_gripper_qpos"].tolist(),
            steps=self.steps,
            remaining_steps=self.max_steps - self.steps,
            success=self.success,
            done=self.done,
        )
        if self.privileged:
            result["object_state"] = {
                key: np.asarray(value).tolist()
                for key, value in self._obs.items()
                if not key.startswith("robot") and key.endswith(("_pos", "_quat"))
            }
        return result

    def _advance(self, action):
        self._obs, reward, _, _ = self._env.step(action)
        self.steps += 1
        self.success = bool(self._env.check_success())
        with (self.output / "actions.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    dict(
                        step=self.steps,
                        action=list(map(float, action)),
                        reward=float(reward),
                        success=self.success,
                    )
                )
                + "\n"
            )
        if self.steps % 5 == 0 or self.done:
            self._record_frame()

    def step(self, action, repeat=1):
        """OSC delta [dx,dy,dz,rx,ry,rz,grip], normalized [-1,1].

        Translation scale is 0.05 m per unit; rotation scale is 0.5 rad per unit.
        Gripper: -1 opens, +1 closes. Every repeat consumes a physics control step.
        """
        action = vector(action, 7, "action")
        if (np.abs(action) > 1).any():
            raise ValueError("All action values must be in [-1, 1]")
        positive_int(repeat, "repeat", maximum=100)
        self.gripper_command = float(action[-1])
        for _ in range(repeat):
            if self.done:
                break
            self._advance(action)
        return self.observe()

    def move_to(self, xyz, max_steps=60, tolerance=0.01):
        """Move toward a world-coordinate XYZ in meters, keeping orientation.

        Feedback controller only; does not plan around obstacles or guarantee reachability.
        Use approach/lift/transfer waypoints. Check `reached` before proceeding.
        """
        target = vector(xyz, 3, "xyz")
        positive_int(max_steps, "max_steps", maximum=100)
        if not np.isfinite(tolerance) or not 0 < tolerance <= 0.05:
            raise ValueError("tolerance must be in (0, 0.05] meters")
        for _ in range(max_steps):
            error = target - self._obs["robot0_eef_pos"]
            if self.done or np.linalg.norm(error) <= tolerance:
                break
            action = np.r_[np.clip(error / 0.05, -1, 1), [0.0, 0.0, 0.0], self.gripper_command]
            self._advance(action)
        result = self.observe()
        result["position_error"] = float(np.linalg.norm(target - self._obs["robot0_eef_pos"]))
        result["reached"] = result["position_error"] <= tolerance
        return result

    def gripper(self, closed, steps=15):
        if not isinstance(closed, bool):
            raise ValueError("closed must be a boolean")
        return self.step([0.0] * 6 + [1.0 if closed else -1.0], repeat=steps)

    def result(self):
        return {
            **self.metadata,
            "success": self.success,
            "steps": self.steps,
            "termination": "success"
            if self.success
            else ("step_limit" if self.done else "policy_returned"),
            "wall_seconds": time.monotonic() - self._started,
            "output": str(self.output),
        }

    def close(self):
        try:
            if self._writer is not None:
                self._writer.close()
                self._writer = None
        finally:
            if self._env is not None:
                self._env.close()
                self._env = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["tasks", "smoke"])
    parser.add_argument("--suite", choices=SUITES, default="libero_goal")
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--init-state-id", type=int, default=0)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if args.command == "tasks":
        suite = load_suite(args.suite)
        for i in range(suite.n_tasks):
            print(f"{i:2d}  {suite.get_task(i).language}")
        return
    output = args.output or f"runs/smoke-{time.time_ns()}"
    with Robot(
        suite=args.suite,
        task_id=args.task_id,
        init_state_id=args.init_state_id,
        max_steps=args.steps,
        output=output,
    ) as robot:
        before = robot.observe()
        # Small upward move demonstrates that actions change the physical state.
        while not robot.done:
            robot.step([0.0, 0.0, 0.1, 0.0, 0.0, 0.0, -1.0], repeat=min(10, args.steps))
        after = robot.observe()
        result = robot.result()
        result["eef_displacement_m"] = float(
            np.linalg.norm(np.array(after["eef_pos"]) - before["eef_pos"])
        )
        (robot.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
