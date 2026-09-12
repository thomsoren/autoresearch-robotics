"""Why does the approach stall before the handle? Report active contacts."""

import numpy as np

from simulation.sim import Robot

HANDLE = np.array([0.0424, -0.1324, 1.0155])

robot = Robot(
    suite="libero_goal", task_id=0, init_state_id=0, seed=0,
    max_steps=300, output="runs/diag-contact/state-000", video=True, privileged=True,
)


def contacts():
    sim = robot._env.env.sim
    model, data = sim.model, sim.data
    out = []
    for i in range(data.ncon):
        c = data.contact[i]
        a = model.geom_id2name(c.geom1) or f"<{c.geom1}>"
        b = model.geom_id2name(c.geom2) or f"<{c.geom2}>"
        if "gripper" in a or "finger" in a or "robot" in a or \
           "gripper" in b or "finger" in b or "robot" in b:
            out.append(f"{a} <-> {b} dist={c.dist:+.5f}")
    return out


def report(label):
    raw = robot.observe()
    print(f"\n{label}: eef={np.round(raw['eef_pos'], 4).tolist()} "
          f"fingers={np.round(raw['gripper_qpos'], 4).tolist()}", flush=True)
    for line in contacts()[:12]:
        print("   ", line, flush=True)


try:
    report("initial")
    # Approach the handle face at handle height, straight in along -y.
    for stage, offset in enumerate([0.12, 0.08, 0.05, 0.03, 0.015]):
        target = HANDLE + np.array([0.0, offset, 0.0])
        result = robot.move_to(target.tolist(), max_steps=60, tolerance=0.008)
        raw = robot.observe()
        print(f"\nstage y_offset={offset:.3f} target_y={target[1]:+.4f} "
              f"actual_y={raw['eef_pos'][1]:+.4f} reached={result['reached']} "
              f"err={result['position_error']*1000:.1f}mm", flush=True)
        for line in contacts()[:12]:
            print("   ", line, flush=True)
finally:
    robot.close()
