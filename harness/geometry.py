"""Franka Panda body geometry, derived from the robosuite MuJoCo model.

The agent sees two 256x256 images and an end-effector pose. Neither tells it how much
space its own hand occupies, nor which way the jaws open. Both matter: the hand is a
solid block roughly 10 cm long sitting directly behind the grasp point, and the jaws
open along one axis only, so a graspable object still has to be approached with the
gripper rolled to match.

Constants below are read off `robosuite/models/assets/grippers/panda_gripper.xml` and
`robosuite/models/assets/robots/panda/robot.xml` (robosuite 1.4.1):

- `grip_site`, which `robot0_eef_pos` reports, sits at hand-local z = 0.097 m. The two
  finger pads span z = 0.085..0.101 m, so the reported position is the midpoint between
  the pads -- it is the grasp point, not the wrist.
- `finger_joint1` travels [0, 0.04] and `finger_joint2` travels [-0.04, 0], so the jaws
  span 0.08 m fully open.
- Link offsets 0.333 + 0.316 + 0.384 + 0.088 + 0.107 + 0.097 give the 0.855 m reach
  quoted in Franka's own datasheet.
"""

import math

# `robot0_eef_quat` is the orientation of the `right_hand` flange body. The gripper is
# mounted on it rotated -90 deg about z, which maps the jaw travel axis (gripper-local y)
# onto flange-local x and leaves the approach axis (gripper-local z) unchanged.
APPROACH_AXIS = (0.0, 0.0, 1.0)
JAW_AXIS = (1.0, 0.0, 0.0)

JAW_MAX_OPENING_M = 0.08
FINGER_LENGTH_M = 0.054
HAND_LENGTH_M = 0.10
MAX_REACH_M = 0.855


def rotation_matrix(quat_xyzw):
    """Rotation matrix from a robosuite (x, y, z, w) quaternion, as nested tuples."""
    x, y, z, w = (float(value) for value in quat_xyzw)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not norm:
        raise ValueError("quaternion must not be zero-length")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def _apply(matrix, axis):
    return [round(sum(row[i] * axis[i] for i in range(3)), 3) for row in matrix]


def body_state(payload):
    """Gripper pose facts that neither camera shows, or None if the pose is absent.

    `approach_dir` is the unit vector the palm faces: the hand occupies the ~10 cm
    directly behind the grasp point along it. `jaw_axis` is the unit vector the fingers
    travel along; an object is graspable only across this axis.
    """
    quat = payload.get("eef_quat_xyzw")
    fingers = payload.get("gripper_qpos")
    if not quat or not fingers or len(fingers) < 2:
        return None
    matrix = rotation_matrix(quat)
    opening = abs(float(fingers[0]) - float(fingers[1]))
    return {
        "approach_dir": _apply(matrix, APPROACH_AXIS),
        "jaw_axis": _apply(matrix, JAW_AXIS),
        "jaw_opening_m": round(opening, 4),
        "jaw_max_opening_m": JAW_MAX_OPENING_M,
    }
