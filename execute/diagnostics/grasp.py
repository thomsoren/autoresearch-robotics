"""Refined rotated grasp: pitch first, correct drift, then straddle the handle.

Key corrections over the previous attempt:
 - re-servo x/z AFTER pitching, since pitch drifts the end-effector position
 - approach to the handle bar's own y, not a standoff, so fingers straddle it
 - verify fingers actually stop on the handle (qpos well above the ~0.0005 m
   that indicates closing on empty air) before pulling
"""

import json
import pathlib

import numpy as np

from simulation.sim import Robot

HANDLE = np.array([0.0424, -0.1353, 1.0154])
OUT = pathlib.Path("runs/diag-grasp")
OUT.mkdir(parents=True, exist_ok=True)


def tool_axis(q):
    x, y, z, w = q
    return np.array([2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)])


def drawer(robot):
    sim = robot._env.env.sim
    i = sim.model.joint_name2id("wooden_cabinet_1_middle_level")
    return float(sim.data.qpos[sim.model.jnt_qposadr[i]])


def blockers(robot):
    sim = robot._env.env.sim
    model, data = sim.model, sim.data
    out = []
    for i in range(data.ncon):
        c = data.contact[i]
        a = model.geom_id2name(c.geom1) or ""
        b = model.geom_id2name(c.geom2) or ""
        if any("gripper" in n for n in (a, b)) and any("cabinet" in n for n in (a, b)):
            out.append(f"{a}|{b}")
    return sorted(set(out))


robot = Robot(suite="libero_goal", task_id=0, init_state_id=0, seed=0,
              max_steps=480, output=str(OUT / "state-000"), video=True,
              privileged=True)
log = []


def show(label):
    raw = robot.observe()
    e = dict(label=label, eef=list(map(float, np.round(raw["eef_pos"], 4))),
             tool_z=list(map(float, np.round(tool_axis(raw["eef_quat_xyzw"]), 3))),
             fingers=list(map(float, np.round(raw["gripper_qpos"], 4))),
             drawer=round(drawer(robot), 4), success=raw["success"],
             blockers=blockers(robot), step=raw["steps"])
    log.append(e)
    print(f"{label:22s} eef={e['eef']} tz={e['tool_z']} f={e['fingers']} "
          f"dr={e['drawer']:+.4f} s={e['success']} b={e['blockers'][:1]}", flush=True)
    return e


try:
    show("initial")
    # Stage clear of the cabinet, level with the handle.
    robot.move_to((HANDLE + np.array([0.0, 0.25, 0.02])).tolist(),
                  max_steps=90, tolerance=0.01)
    show("staged")

    # Pitch about +y in small settled increments until the tool points along -y.
    for _ in range(16):
        robot.step([0.0, 0.0, 0.0, 0.0, 0.3, 0.0, -1.0], repeat=2)
        axis = tool_axis(robot.observe()["eef_quat_xyzw"])
        if axis[1] < -0.85:   # tool now points toward the cabinet face
            break
    show("pitched")

    # Pitch moved the arm; servo back to the handle's x and z while standing off.
    robot.move_to([HANDLE[0], HANDLE[1] + 0.18, HANDLE[2]],
                  max_steps=80, tolerance=0.008)
    show("realigned")

    # Creep in along -y until the fingers straddle the handle bar.
    for offset in (0.12, 0.09, 0.07, 0.055, 0.045):
        robot.move_to([HANDLE[0], HANDLE[1] + offset, HANDLE[2]],
                      max_steps=50, tolerance=0.006)
        e = show(f"in y+{offset:.3f}")
        if e["blockers"]:
            print("   blocked here", flush=True)
            break

    robot.gripper(closed=True, steps=25)
    e = show("closed")
    grasped = e["fingers"][0] > 0.004
    print(f"   fingers stopped on something: {grasped} "
          f"(qpos {e['fingers'][0]:.4f}; ~0.0005 means empty air)", flush=True)

    for i in range(12):
        cur = np.array(robot.observe()["eef_pos"], dtype=float)
        robot.move_to((cur + np.array([0.0, 0.028, 0.0])).tolist(),
                      max_steps=18, tolerance=0.006)
        e = show(f"pull {i+1}")
        if e["success"]:
            print(">>> LIBERO SUCCESS", flush=True)
            break

    result = robot.result()
    print("RESULT success=%s steps=%s drawer=%+.4f"
          % (result["success"], result["steps"], drawer(robot)), flush=True)
    (OUT / "grasp.json").write_text(json.dumps(
        dict(result=result, log=log), indent=2) + "\n")
finally:
    robot.close()
