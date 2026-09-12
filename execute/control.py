"""Bounded pose feedback over normalized OSC; contains no task geometry."""

import json

import numpy as np
from scipy.spatial.transform import Rotation

from simulation.sim import positive_int, vector


def rotation_from_6d(values):
    """First two rotation-matrix columns, projected to a right-handed frame."""
    values = vector(values, 6, "rotation axes")
    x, y = values[:3], values[3:]
    if np.linalg.norm(x) < 1e-4:
        raise ValueError("Orientation x axis is zero; use an intermediate orientation")
    x = x / np.linalg.norm(x)
    y = y - np.dot(x, y) * x
    if np.linalg.norm(y) < 1e-4:
        raise ValueError("Orientation axes are parallel; use an intermediate orientation")
    y = y / np.linalg.norm(y)
    return np.column_stack((x, y, np.cross(x, y)))


def pose_precheck(waypoints):
    """Stock Inspect motion hook: malformed orientations are correctable tool errors."""
    previous = None
    try:
        for waypoint in waypoints:
            rotation = rotation_from_6d(waypoint[3:9])
            if previous is not None:
                angle = Rotation.from_matrix(rotation @ previous.T).magnitude()
                if angle > 0.35:
                    return (
                        "Orientation interpolation jumps too far; use an intermediate orientation"
                    )
            previous = rotation
    except ValueError as exc:
        return str(exc)
    return None


def servo_pose(
    robot,
    xyz,
    rotation,
    *,
    grip=None,
    max_steps=30,
    min_steps=1,
    position_tolerance=0.001,
    rotation_tolerance=0.02,
    chunk_final=None,
):
    """Execute one waypoint and report measured residuals, never task success by inference.

    Position and rotation refer to the SAME grip site in world coordinates.
    Every OSC command advances counted physics, including settling/gripper commands.
    A stopped/unreachable motion returns its residual; callers must re-observe.
    """
    target = vector(xyz, 3, "target position")
    rotation = np.asarray(rotation, dtype=float)
    if (
        rotation.shape != (3, 3)
        or not np.isfinite(rotation).all()
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
        or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-5)
    ):
        raise ValueError("Target rotation must be a proper orthonormal matrix")
    positive_int(max_steps, "max_steps", maximum=100)
    positive_int(min_steps, "min_steps", maximum=max_steps)
    for tolerance in (position_tolerance, rotation_tolerance):
        if not np.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("Pose tolerances must be positive and finite")
    if grip is not None and (not np.isfinite(grip) or grip not in (-1, 1)):
        raise ValueError("grip must be -1 (open), +1 (close), or omitted")
    if grip is not None:
        robot.gripper_command = float(grip)
    start = robot.steps
    initial_pos, initial_rot = robot.pose()
    best = float("inf")
    stalled = 0
    stop = "motion_limit"
    for _ in range(max_steps):
        position, current = robot.pose()
        error = target - position
        angular = Rotation.from_matrix(rotation @ current.T).as_rotvec()
        if robot.done:
            stop = "success" if robot.success else "episode_limit"
            break
        if (
            robot.steps - start >= min_steps
            and np.linalg.norm(error) <= position_tolerance
            and np.linalg.norm(angular) <= rotation_tolerance
        ):
            stop = "reached"
            break
        metric = np.linalg.norm(error) + 0.05 * np.linalg.norm(angular)
        if metric < best - 1e-5:
            best, stalled = metric, 0
        else:
            stalled += 1
        if stalled >= 12 and robot.steps - start >= min_steps:
            stop = "stalled"
            break
        # Limit physical goal offsets, independently of the target's distance.
        translation = error * min(1.0, 0.01 / max(np.linalg.norm(error), 1e-12))
        angular *= min(1.0, 0.10 / max(np.linalg.norm(angular), 1e-12))
        action = np.r_[translation / 0.05, angular / 0.5, robot.gripper_command]
        robot._advance(action)
    position, current = robot.pose()
    position_error = float(np.linalg.norm(target - position))
    rotation_error = float(Rotation.from_matrix(rotation @ current.T).magnitude())
    reached = position_error <= position_tolerance and rotation_error <= rotation_tolerance
    if robot.done:
        stop = "success" if robot.success else "episode_limit"
    elif reached and robot.steps - start >= min_steps:
        stop = "reached"
    record = {
        "start_step": start,
        "end_step": robot.steps,
        "requested_position": target.tolist(),
        "requested_rotation": rotation.tolist(),
        "before_position": initial_pos.tolist(),
        "before_rotation": initial_rot.tolist(),
        "achieved_position": position.tolist(),
        "achieved_rotation": current.tolist(),
        "position_error": position_error,
        "rotation_error": rotation_error,
        "reached": bool(reached),
        "stop_reason": stop,
        "gripper_command": robot.gripper_command,
        "inspect_chunk_final": chunk_final,
    }
    with (robot.output / "control.jsonl").open("a") as stream:
        stream.write(json.dumps(record, allow_nan=False) + "\n")
    return {**robot.observe(), **record}
