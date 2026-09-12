"""Scripted scene inspection: where is the middle drawer handle, and what does
LIBERO's success predicate actually require? Privileged state is used for
DIAGNOSIS ONLY and is never exposed to the policy."""

import pathlib
import sys

import numpy as np

from simulation.sim import Robot

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "runs/diag-scene")
OUT.mkdir(parents=True, exist_ok=True)

robot = Robot(
    suite="libero_goal", task_id=0, init_state_id=0, seed=0,
    max_steps=30, output=str(OUT / "scene"), video=False, privileged=True,
)
try:
    raw = robot.observe()
    print("instruction:", raw["instruction"])
    print("eef_pos:", np.round(raw["eef_pos"], 4).tolist())
    print("eef_quat_xyzw:", np.round(raw["eef_quat_xyzw"], 4).tolist())
    print("gripper_qpos:", np.round(raw["gripper_qpos"], 5).tolist())
    print("--- privileged object_state ---")
    for key, value in sorted(raw["object_state"].items()):
        print(f"  {key}: {np.round(value, 4).tolist()}")

    env = robot._env.env
    print("--- joint / site names containing drawer or cabinet ---")
    model = env.sim.model
    for i in range(model.njnt):
        name = model.joint_id2name(i)
        if name and ("drawer" in name.lower() or "cabinet" in name.lower()):
            addr = model.jnt_qposadr[i]
            print(f"  joint {name}: qpos={float(env.sim.data.qpos[addr]):.5f} "
                  f"range={np.round(model.jnt_range[i], 4).tolist()}")
    for i in range(model.nsite):
        name = model.site_id2name(i)
        if name and ("drawer" in name.lower() or "handle" in name.lower()
                     or "cabinet" in name.lower()):
            print(f"  site {name}: xpos="
                  f"{np.round(env.sim.data.site_xpos[i], 4).tolist()}")
    for i in range(model.nbody):
        name = model.body_id2name(i)
        if name and ("drawer" in name.lower() or "cabinet" in name.lower()):
            print(f"  body {name}: xpos="
                  f"{np.round(env.sim.data.body_xpos[i], 4).tolist()}")

    print("--- success predicate source ---")
    bddl = pathlib.Path(robot.metadata.get("bddl_file", "")) if robot.metadata.get("bddl_file") else None
    print("metadata keys:", sorted(robot.metadata.keys()))
finally:
    robot.close()
