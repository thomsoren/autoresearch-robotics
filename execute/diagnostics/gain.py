"""Scripted (no-LLM) diagnostic: commanded vs achieved EEF displacement.

Measures the steady-state gain of the normalized OSC translation command in free
space, so controller behaviour is separated from contact/obstruction.
"""

import json
import pathlib
import sys

import numpy as np

from simulation.sim import Robot

OUT = sys.argv[1] if len(sys.argv) > 1 else "runs/diag-gain"


def run_case(axis, magnitude, steps, output):
    """Hold one constant normalized command and log per-step achieved motion."""
    robot = Robot(
        suite="libero_goal",
        task_id=0,
        init_state_id=0,
        seed=0,
        max_steps=steps + 5,
        output=output,
        video=False,
    )
    try:
        action = [0.0] * 7
        action[axis] = magnitude
        action[6] = -1.0  # keep gripper open; do not disturb the measurement
        start = np.array(robot.observe()["eef_pos"], dtype=float)
        previous = start.copy()
        per_step = []
        for _ in range(steps):
            raw = robot.step(action)
            current = np.array(raw["eef_pos"], dtype=float)
            per_step.append(float(current[axis] - previous[axis]))
            previous = current
        total = float(previous[axis] - start[axis])
        commanded_per_step = magnitude * 0.05
        tail = per_step[-5:]
        return dict(
            axis="xyz"[axis],
            magnitude=magnitude,
            steps=steps,
            commanded_per_step_m=commanded_per_step,
            commanded_total_m=commanded_per_step * steps,
            achieved_total_m=total,
            total_ratio=total / (commanded_per_step * steps),
            first_step_m=per_step[0],
            steady_step_m=float(np.mean(tail)),
            steady_ratio=float(np.mean(tail)) / commanded_per_step,
            per_step_m=per_step,
        )
    finally:
        robot.close()


cases = []
for axis in (0, 1, 2):
    for magnitude in (0.2, 0.5, 1.0):
        case = run_case(axis, magnitude, 20, f"{OUT}/axis{axis}-mag{magnitude}")
        cases.append(case)
        print(
            f"axis={case['axis']} mag={magnitude:>4} "
            f"cmd/step={case['commanded_per_step_m']*1000:6.2f}mm "
            f"first={case['first_step_m']*1000:6.2f}mm "
            f"steady={case['steady_step_m']*1000:6.2f}mm "
            f"steady_ratio={case['steady_ratio']:.3f} "
            f"total_ratio={case['total_ratio']:.3f}",
            flush=True,
        )

path = f"{OUT}/gain.json"
pathlib.Path(OUT).mkdir(parents=True, exist_ok=True)
pathlib.Path(path).write_text(json.dumps(cases, indent=2) + "\n")
print("wrote", path)
