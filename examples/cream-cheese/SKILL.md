---
name: robot_operating_guide
description: >
  General operating guide for LIBERO manipulation tasks requiring pick-and-place
  of objects into containers. Covers perception-guided approach, grasp verification,
  budget-aware retry, low-release placement and post-place confirmation.
---

# Robot Operating Guide

## When to use
Apply this guide whenever the task requires grasping a target object from a surface
and placing it into or onto a container or goal location.

## Perception before approach
- Use `locate_pixels` on both the target object and the goal container in the initial
  observation to obtain world-surface positions. Query multiple pixels on the object
  to estimate its lateral extent and on the container rim to record its height.
- Prefer the wrist camera for fine-grained pre-grasp alignment; use the agent view
  for global layout.
- Surface positions from `locate_pixels` are visible-surface points, not object
  centers. Account for object height and finger thickness when choosing grasp depth.

## Approach and orientation
- Move the end-effector above the object with fingers open before descending.
- Choose an orientation so that the closing axis of the fingers spans the object's
  narrower dimension, confirmed visually or from depth measurements.
- Descend in two stages: first to a safe hover height, then to grasp depth, so
  each stage can be visually checked.

## Grasp and verification
- After commanding grip close, read `finger_qpos` in the next observation.
- **Fully closed (both fingers near 0 m gap):** the gripper missed the object.
  Open immediately, raise slightly, make a small lateral or depth adjustment based
  on the wrist view, and retry. Do not repeat the identical target.
- **Partially closed at a width consistent with the object:** treat as contact.
  Attempt a small lift (raise z by 2–3 cm while keeping grip closed) and check
  that the arm actually rises; if the object moves with the arm, the grasp is valid.
- Limit grasp retries to three attempts. If all fail, re-query `locate_pixels`
  from the current position to refresh the target estimate before a fourth attempt.
  If the object cannot be grasped after four total attempts, call `give_up`.

## Transport
- After a verified grasp, move in a single waypoint to a position above the
  destination container, keeping `grip=1`.
- Choose a transport height that clears any objects between source and destination.
- Check `finger_qpos` after transport: if the fingers have fully opened the object
  was dropped in transit; note the drop location and re-grasp if budget allows.

## Placement and release
- **Before descending to release height**, query `locate_pixels` on the container
  rim or interior to measure its current world-z. Do not rely on an estimate from
  the initial observation; the measured depth from the transport position is more
  accurate.
- Lower the gripper until the held object is at or just inside the container rim.
  Target the release z so the object's bottom face is at or below the measured rim
  height — the gripper tip should be inside or flush with the rim before opening.
  A drop gap larger than 2–3 cm above the rim risks ejection from shallow containers.
- Open the gripper and raise the arm clear of the container.

## Post-place verification and completion
- After release and raising clear, take a fresh observation and inspect both the
  agent view and wrist view before calling `done`.
- Confirm that the object is visibly resting inside the container interior, not on
  the rim or on the table nearby.
- If the object is on the rim or nearby but not inside, attempt one gentle nudge
  by re-grasping and re-placing, if budget remains. Do not call `done` until
  placement is confirmed or budget is exhausted.
- If the object is clearly outside the container and budget is low, call `give_up`
  rather than using remaining calls unproductively.

## Budget discipline
- Reserve at least two LLM calls for transport and three for
  placement+rim-query+verification.
- Count remaining budget from the `remaining_steps` field (physics steps) and the
  known call count; if fewer than four calls remain and the object is not yet
  grasped, prioritize a single best-effort grasp-and-drop over further retries.
- Do not loop on identical failed targets; each retry must use updated position
  information from a fresh observation or `locate_pixels` call.

## Progress checks
- After each `move_to`, read `motion_reached`, `motion_position_error` and
  `motion_rotation_error`. If `motion_reached=0` and position error is large,
  the arm stalled; choose a new waypoint from the measured pose rather than
  retrying the same target.
- Closed fingers do not establish a grasp. Only partial finger closure at a width
  consistent with the object, confirmed across at least one physics step, counts
  as a grasp signal.
