"""Body-geometry tests. Pure arithmetic on a pose: no LIBERO, no MuJoCo."""

import pytest

from harness.geometry import JAW_MAX_OPENING_M, body_state, rotation_matrix

IDENTITY = [0.0, 0.0, 0.0, 1.0]
ROLL_90_Z = [0.0, 0.0, 0.7071068, 0.7071068]
FLIP_180_X = [1.0, 0.0, 0.0, 0.0]


def pose(quat=IDENTITY, fingers=(0.04, -0.04)):
    return {"eef_quat_xyzw": list(quat), "gripper_qpos": list(fingers)}


def test_neutral_pose_points_the_palm_along_z_and_opens_along_x():
    body = body_state(pose())
    assert body["approach_dir"] == [0.0, 0.0, 1.0]
    assert body["jaw_axis"] == [1.0, 0.0, 0.0]


def test_rolling_the_wrist_rotates_the_jaw_axis_but_not_the_approach():
    body = body_state(pose(quat=ROLL_90_Z))
    assert body["jaw_axis"] == [0.0, 1.0, 0.0]
    assert body["approach_dir"] == [0.0, 0.0, 1.0]


def test_flipping_the_wrist_points_the_palm_down():
    assert body_state(pose(quat=FLIP_180_X))["approach_dir"] == [0.0, 0.0, -1.0]


@pytest.mark.parametrize(
    ("fingers", "expected"),
    [((0.04, -0.04), 0.08), ((0.0, 0.0), 0.0), ((0.015, -0.015), 0.03)],
)
def test_jaw_opening_is_the_gap_between_the_two_fingers(fingers, expected):
    assert body_state(pose(fingers=fingers))["jaw_opening_m"] == pytest.approx(expected)


def test_reported_opening_never_exceeds_the_advertised_maximum():
    assert body_state(pose())["jaw_opening_m"] == JAW_MAX_OPENING_M


@pytest.mark.parametrize(
    "payload",
    [{}, {"eef_quat_xyzw": IDENTITY}, {"gripper_qpos": [0.0, 0.0]}, {"eef_quat_xyzw": IDENTITY, "gripper_qpos": [0.0]}],
)
def test_incomplete_pose_yields_no_body_block(payload):
    assert body_state(payload) is None


def test_unnormalised_quaternion_is_normalised_not_rejected():
    body = body_state(pose(quat=[0.0, 0.0, 0.0, 5.0]))
    assert body["approach_dir"] == [0.0, 0.0, 1.0]


def test_zero_quaternion_is_rejected():
    with pytest.raises(ValueError):
        rotation_matrix([0.0, 0.0, 0.0, 0.0])
