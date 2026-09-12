"""Is the 0.25 gain a settling-time effect or a permanent scale error?

Case A: hold a constant command for N steps (what the adapter does).
Case B: command once, then send zero-translation steps and watch it converge.
Case C: closed-loop move_to a measured 10 cm target.
"""

import json
import pathlib
import sys

import numpy as np

from simulation.sim import Robot

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "runs/diag-settle")
OUT.mkdir(parents=True, exist_ok=True)


def fresh(output, max_steps=200):
    return Robot(
        suite="libero_goal", task_id=0, init_state_id=0, seed=0,
        max_steps=max_steps, output=str(output), video=False,
    )


report = {}

# Case B: one nonzero command, then hold zero translation and watch settling.
robot = fresh(OUT / "hold-zero")
try:
    start = np.array(robot.observe()["eef_pos"], dtype=float)
    robot.step([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0])
    trace = [float(np.array(robot.observe()["eef_pos"])[0] - start[0])]
    for _ in range(24):
        raw = robot.step([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0])
        trace.append(float(np.array(raw["eef_pos"], dtype=float)[0] - start[0]))
    report["single_command_then_zero"] = dict(
        commanded_offset_m=0.05, x_after_each_step_m=trace, final_m=trace[-1],
        note="one unit command = 0.05 m goal offset; zeros let the controller settle",
    )
    print("single cmd 0.05 m goal -> after 1 step %.2f mm, settled %.2f mm"
          % (trace[0] * 1000, trace[-1] * 1000), flush=True)
finally:
    robot.close()

# Case C: closed-loop move_to over a 10 cm displacement.
robot = fresh(OUT / "move-to")
try:
    start = np.array(robot.observe()["eef_pos"], dtype=float)
    target = start + np.array([0.0, 0.0, 0.10])
    result = robot.move_to(target.tolist(), max_steps=100, tolerance=0.01)
    report["move_to_10cm_z"] = dict(
        start=start.tolist(), target=target.tolist(),
        final=list(map(float, result["eef_pos"])),
        position_error_m=result["position_error"], reached=result["reached"],
        steps_used=result["steps_remaining"] if "steps_remaining" in result else None,
    )
    print("move_to 10 cm +z: reached=%s error=%.2f mm"
          % (result["reached"], result["position_error"] * 1000), flush=True)
finally:
    robot.close()

(OUT / "settle.json").write_text(json.dumps(report, indent=2) + "\n")
print("wrote", OUT / "settle.json")
