"""Calibrated sensor checks with real rendered depth; no object state to the actor."""

import numpy as np

from simulation.sim import Robot


def test_depth_projection_table_plane_and_moving_wrist_calibration(tmp_path):
    with Robot(task_id=5, max_steps=10, output=tmp_path / "sensor", video=False,
               calibrated_depth=True) as robot:
        before = robot.observe()
        assert robot.metadata["observation_mode"] == "calibrated_rgbd"
        assert "object_state" not in before
        geometry = before["camera_geometry"]["agentview"]
        with np.load(geometry["depth_path"], allow_pickle=False) as saved:
            depth = saved["depth"]
        assert depth.shape == (256, 256)
        pixel = np.array([230, 230, 1])
        camera_point = np.linalg.solve(geometry["intrinsics"], pixel) * depth[230, 230]
        world_point = np.asarray(geometry["camera_to_world"]) @ np.r_[camera_point, 1]
        table_z = robot._env.env.workspace_offset[2]
        assert abs(world_point[2] - table_z) < 0.005
        after = robot.step([0, 0, 0.1, 0, 0, 0, -1], repeat=10)
        np.testing.assert_allclose(
            geometry["camera_to_world"],
            after["camera_geometry"]["agentview"]["camera_to_world"],
        )
        old_wrist = np.asarray(before["camera_geometry"]["robot0_eye_in_hand"]["camera_to_world"])
        new_wrist = np.asarray(after["camera_geometry"]["robot0_eye_in_hand"]["camera_to_world"])
        assert np.linalg.norm(old_wrist[:3, 3] - new_wrist[:3, 3]) > 0.001
        assert geometry["step"] == 0
        assert after["camera_geometry"]["agentview"]["step"] == 10
