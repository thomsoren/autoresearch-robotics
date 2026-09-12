"""Locate the middle drawer's handle geometry precisely (diagnostic only)."""

import numpy as np

from simulation.sim import Robot

robot = Robot(
    suite="libero_goal", task_id=0, init_state_id=0, seed=0,
    max_steps=20, output="runs/diag-geom/scene", video=False, privileged=True,
)
try:
    sim = robot._env.env.sim
    model, data = sim.model, sim.data

    print("=== bodies under the cabinet, with their geoms ===")
    for i in range(model.nbody):
        name = model.body_id2name(i)
        if not name or "cabinet" not in name.lower():
            continue
        print(f"body {name}: xpos={np.round(data.body_xpos[i], 4).tolist()}")
        for g in range(model.ngeom):
            if model.geom_bodyid[g] != i:
                continue
            gname = model.geom_id2name(g) or f"<geom{g}>"
            print(f"    geom {gname}: pos={np.round(data.geom_xpos[g], 4).tolist()} "
                  f"size={np.round(model.geom_size[g], 4).tolist()} "
                  f"type={int(model.geom_type[g])}")
finally:
    robot.close()
