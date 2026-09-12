> Historical diagnosis at commit `3b14746`. The integrated branch implements
> feedback pose control and corrects the measured orientation frame. See
> [README.md](README.md) for the current contract. Original measurements below
> remain diagnostic evidence, not an exhaustive proof of task impossibility.

# Control diagnosis: LIBERO Goal task 0, state 0

Scripted, no-LLM measurements of why the Inspect executor stalls before the
middle drawer. Reproduce with `execute/diagnostics/`; the numbers are pinned by
`execute/test_control_diagnosis.py`. Environment: WSL Ubuntu 24.04, MuJoCo
3.2.7, LIBERO `8f1084e`, 88 tests passing.

## Summary

Two independent problems. The second is the blocking one.

1. **Commanded motion is a goal offset, not a displacement.** The adapter maps a
   requested displacement open-loop with `values[:3] / 0.05`, assuming a command
   completes within one control step. It does not: the measured steady-state
   gain is ~0.25, so the agent receives about a quarter of what it asks for.
2. **The fixed wrist orientation makes the grasp kinematically impossible.** The
   gripper's palm collides with cabinet handle geometry ~105 mm before reaching
   the middle handle, in every approach tried. No command-scale fix can
   compensate for this.

The earlier suggestion that collisions explain the shortfall is **half right**,
and the two halves were conflated. Collisions do not cause the 1/4 shortfall
(that is present in free space with zero contact), but a collision *is* what
prevents the grasp.

## 1. Command semantics, not obstruction

Free-space sweep, 3 axes x magnitudes {0.2, 0.5, 1.0}, 20 steps each
(`diagnostics/gain.py`):

| Axis | Magnitude | Commanded/step | Steady/step | Steady ratio |
| --- | --- | --- | --- | --- |
| x | 0.2 / 0.5 / 1.0 | 10 / 25 / 50 mm | 2.56 / 6.47 / 12.34 mm | 0.256 / 0.259 / 0.247 |
| y | 0.2 / 0.5 / 1.0 | 10 / 25 / 50 mm | 2.59 / 6.54 / 12.86 mm | 0.259 / 0.261 / 0.257 |
| z | 0.2 / 0.5 / 1.0 | 10 / 25 / 50 mm | 2.57 / 6.44 / 12.58 mm | 0.257 / 0.258 / 0.252 |

The ratio is constant across every axis and magnitude, with **no contact at
all**. That rules out both obstruction and actuator saturation as the cause.
The first step achieves only ~0.09 of the command before rising to the plateau.

Holding one 0.05 m goal and then sending zero-translation steps settles at
13.90 mm, not 50 mm (`diagnostics/settle.py`), so this is not merely unfinished
settling. `robosuite` `osc_pose.json` uses `output_max` 0.05 m, `kp` 150,
`damping_ratio` 1, `control_delta` true at 20 Hz.

By contrast the existing closed-loop path already works: `Robot.move_to` over
10 cm in +z reports `reached=True` with 7.21 mm final error.

**Implication.** Inspect chunks `move_by` to <=0.01 m per axis per step, so each
chunk lands ~2.5 mm. Opening the drawer needs 14 cm of travel, i.e. ~14 perfect
chunks but **>55** at the measured gain.

## 2. The success criterion (undocumented until now)

`diagnostics/axis.py` drives the joint directly:

- Success requires `wooden_cabinet_1_middle_level` qpos **< -0.14 m**; range is
  `[-0.16, 0.01]` starting at 0.0. `check_success()` is False at exactly -0.14
  and True at -0.16, so the comparison is strict and the drawer must travel
  ~14 cm, 87.5% of full travel.
- The drawer slides along world **+y**; the handle travels y -0.135 -> +0.025.
- Middle handle bar = geoms `wooden_cabinet_1_g28`/`g29` at
  `[0.0424, -0.1353, 1.0154]`, with mounting posts `g30`/`g31` at x 0.0104 and
  0.0752.

## 3. The blocking collision

`diagnostics/reach.py` swept 12 fixed-orientation approaches (dz in
-0.04..0.02, dx in -0.03..0.03), driving straight in along -y:

- **Every** variant stalled at y between -0.0264 and -0.0309, leaving a
  **104-109 mm gap** to the handle at y = -0.1353.
- **Every** variant was blocked by `gripper0_hand_collision` against
  `wooden_cabinet_1_g18` (the *top* drawer's handle) or `g29` (the middle
  handle itself). The palm strikes the handle broadside; the fingers never
  straddle it.
- Fingers closing to ~0.0005 m confirm they closed on empty air.

This is not a workspace limit. `diagnostics/reach2.py` commands the same depth
directly above the cabinet and reaches y = -0.1351 with 6.0 mm error and
`reached=True`. The handle's depth is comfortably reachable; only the approach
at handle height is obstructed.

Allowing bounded wrist pitch through `Robot.step`'s rotation channels
(`diagnostics/grasp.py`) reached y = -0.025 and produced the **first nonzero
drawer motion observed in any run** (qpos +0.0017) by contacting the middle
handle. It still did not grasp: the pitch drifts the end-effector's x position
and the achieved tool axis saturated around -0.08 in y rather than the -0.85
needed to face the handle squarely. Rotation is necessary but a working grasp
was not achieved.

## Recommendation

1. **Close the loop in the adapter.** Replace the open-loop `values[:3] / 0.05`
   in `LiberoEmbodiment.step` with servoing on measured position toward the
   requested displacement, reusing the `move_to` feedback that already reaches
   targets within ~7 mm. This makes `move_by`'s documented "world meters"
   contract true. Rescaling the constant instead would still drift under
   contact, where the gain is not 0.25.
2. **Expose wrist pitch.** The fixed orientation is the blocking constraint, and
   this is the "smallest viable change" the handoff asks to be justified with
   evidence. It is a public-contract change, so it needs Thomas's agreement, and
   he must collect a fresh baseline afterwards before any skill gain is
   attributed.

## Not established

- **No LIBERO success has been achieved**, by any script or model. The best
  result is 0.0017 of the 0.14 required.
- Neither recommendation is implemented; both change the public tool contract.
- Only state 0 was examined. States 1 and 2 are untouched, 3-7 are held out.
- No live-model run was made in this session, so nothing here reflects agent
  behaviour; these are controller and scene properties only.
- The Fable 5.1 `reasoning_extraction` refusal is unexplained and untouched.
