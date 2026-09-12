"""Is y=-0.135 reachable at all, or is the stall a workspace limit?

Probe A: descend to the handle's y at a SAFE x far from the cabinet (x=-0.25),
         where no cabinet geometry can interfere.
Probe B: same y, at the handle's x, but high above the cabinet (z=1.25).
If A succeeds, the arm can reach that depth and the stall is purely collision.
"""

import numpy as np

from simulation.sim import Robot

HANDLE = np.array([0.0424, -0.1353, 1.0154])


def blockers(robot):
    sim = robot._env.env.sim
    model, data = sim.model, sim.data
    out = []
    for i in range(data.ncon):
        c = data.contact[i]
        a = model.geom_id2name(c.geom1) or ""
        b = model.geom_id2name(c.geom2) or ""
        if any("gripper" in n or "robot0" in n for n in (a, b)):
            if not any("finger1_pad" in n and "finger2_pad" in m
                       for n, m in ((a, b), (b, a))):
                out.append(f"{a}|{b}")
    return sorted(set(out))


for tag, target in (
    ("A_far_x_same_y", [-0.25, HANDLE[1], HANDLE[2]]),
    ("B_above_cabinet", [HANDLE[0], HANDLE[1], 1.25]),
    ("C_deeper_far_x", [-0.25, -0.20, HANDLE[2]]),
):
    robot = Robot(suite="libero_goal", task_id=0, init_state_id=0, seed=0,
                  max_steps=250, output=f"runs/diag-reach2/{tag}", video=False,
                  privileged=True)
    try:
        result = robot.move_to(target, max_steps=100, tolerance=0.01)
        raw = robot.observe()
        print(f"{tag:18s} target={np.round(target,4).tolist()} "
              f"actual={np.round(raw['eef_pos'],4).tolist()} "
              f"reached={result['reached']} err={result['position_error']*1000:.1f}mm "
              f"block={blockers(robot)[:2]}", flush=True)
    finally:
        robot.close()
