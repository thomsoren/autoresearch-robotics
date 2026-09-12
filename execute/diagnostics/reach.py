"""Can the fixed-orientation gripper ever reach the middle handle?

Sweep approach heights and x offsets, driving straight in along -y, and record
the closest y reached plus the blocking contact. Purely kinematic feasibility.
"""

import itertools
import json
import pathlib

import numpy as np

from simulation.sim import Robot

HANDLE = np.array([0.0424, -0.1353, 1.0154])
OUT = pathlib.Path("runs/diag-reach")
OUT.mkdir(parents=True, exist_ok=True)


def blockers(robot):
    sim = robot._env.env.sim
    model, data = sim.model, sim.data
    found = []
    for i in range(data.ncon):
        c = data.contact[i]
        a = model.geom_id2name(c.geom1) or f"<{c.geom1}>"
        b = model.geom_id2name(c.geom2) or f"<{c.geom2}>"
        pair = (a, b)
        if any("gripper" in n or "robot0" in n for n in pair) and \
           any("cabinet" in n for n in pair):
            found.append(f"{a}|{b}")
    return sorted(set(found))


rows = []
for dz, dx in itertools.product((-0.04, -0.02, 0.0, 0.02), (0.0, -0.03, 0.03)):
    tag = f"dz{dz:+.2f}_dx{dx:+.2f}".replace(".", "p")
    robot = Robot(suite="libero_goal", task_id=0, init_state_id=0, seed=0,
                  max_steps=200, output=str(OUT / tag), video=False,
                  privileged=True)
    try:
        stage = HANDLE + np.array([dx, 0.14, dz])
        robot.move_to(stage.tolist(), max_steps=80, tolerance=0.01)
        goal = HANDLE + np.array([dx, 0.005, dz])
        result = robot.move_to(goal.tolist(), max_steps=80, tolerance=0.006)
        raw = robot.observe()
        y = float(raw["eef_pos"][1])
        gap = y - HANDLE[1]
        row = dict(dz=dz, dx=dx, reached_y=y, gap_to_handle_m=gap,
                   reached=result["reached"], blockers=blockers(robot))
        rows.append(row)
        print(f"dz={dz:+.2f} dx={dx:+.2f} y={y:+.4f} gap={gap*1000:+6.1f}mm "
              f"reached={result['reached']} block={row['blockers'][:1]}", flush=True)
    finally:
        robot.close()

(OUT / "reach.json").write_text(json.dumps(rows, indent=2) + "\n")
best = min(rows, key=lambda r: abs(r["gap_to_handle_m"]))
print("\nCLOSEST:", json.dumps(best, indent=2))
