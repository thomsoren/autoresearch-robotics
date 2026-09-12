"""Determine the drawer's true slide axis and the handle's world pose by
directly driving the joint, rather than inferring direction from coordinates."""

import numpy as np

from simulation.sim import Robot

robot = Robot(suite="libero_goal", task_id=0, init_state_id=0, seed=0,
              max_steps=20, output="runs/diag-axis/scene", video=False,
              privileged=True)
try:
    sim = robot._env.env.sim
    model, data = sim.model, sim.data
    jid = model.joint_name2id("wooden_cabinet_1_middle_level")
    adr = model.jnt_qposadr[jid]

    print("joint axis (local):", np.round(model.jnt_axis[jid], 4).tolist())
    print("joint range:", np.round(model.jnt_range[jid], 4).tolist())
    print("body of joint:", model.body_id2name(model.jnt_bodyid[jid]))

    handle_geoms = [g for g in range(model.ngeom)
                    if (model.geom_id2name(g) or "") in
                    ("wooden_cabinet_1_g28", "wooden_cabinet_1_g29")]

    def handle_pos():
        return np.mean([data.geom_xpos[g] for g in handle_geoms], axis=0)

    print("\nhandle at qpos=0.00 :", np.round(handle_pos(), 4).tolist())
    for target in (-0.05, -0.14, -0.16):
        data.qpos[adr] = target
        sim.forward()
        print(f"handle at qpos={target:+.2f}:", np.round(handle_pos(), 4).tolist(),
              " success=", robot._env.check_success())

    data.qpos[adr] = -0.16
    sim.forward()
    open_pos = handle_pos()
    data.qpos[adr] = 0.0
    sim.forward()
    closed_pos = handle_pos()
    direction = open_pos - closed_pos
    print("\nopening displacement vector:", np.round(direction, 4).tolist())
    print("unit direction:", np.round(direction / np.linalg.norm(direction), 3).tolist())
    print("=> pull the handle along this direction to open")
finally:
    robot.close()
