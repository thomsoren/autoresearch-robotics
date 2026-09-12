# Reusable Robot Skills Implementation Plan

> Execute with superpowers:subagent-driven-development; review changes before completion.

**Goal:** Deliver the approved two-agent skill-learning architecture on existing LIBERO/Inspect code.
**Architecture:** A fixed acting policy uses feedback pose control. An evidence-only improver
revises frozen Markdown memory; the coordinator retains measured development gains.
**Tech Stack:** Python 3.11, MuJoCo 3.2.7, robosuite 1.4.1, Inspect Robots, Claude Agent SDK.
**Spec:** docs/ARCHITECTURE.md

## Global Constraints

- Development states are 0,1,2. Do not consume states 3-7 during implementation.
- Only LIBERO's predicate establishes success.
- Freeze model, base prompt, code, observations and budgets during skill comparisons.
- Gripper-tip testing is excluded. Preserve existing diagnostics and original worktree.

## Task 1: Feedback control and measured evidence

Files: simulation/sim.py, execute/inspect_agent.py, execute/control.py,
execute/perception.py, execute/operational_policy.py, execute/reactive_controller.py
and focused control, camera, policy and controller tests.

- [x] Reproduce a 1 cm request failing a 2 mm position-error assertion in real simulation.
- [x] Inspect OSC orientation semantics and stock policy-supported action contracts.
- [x] Implement bounded measured pose feedback, including gripper-only actuation,
  finite/bounds validation and strict physics budgets.
- [x] Record request/achieved pose, residual and physics-step interval in control.jsonl.
- [x] Test translation, rotation, blocked targets, gripper persistence and exhaustion.
- [x] Capture synchronized upright RGB/depth, intrinsics and observation-local camera
  transforms; expose bounded visible-pixel world-surface queries without object state.
- [x] Clarify stock move schemas and operational note fields without replacing stock
  action validation or execution.
- [x] Replan after measured stalls or motion limits through the public Controller API,
  while preserving the remainder of successful action chunks.

## Task 2: Grounded reusable skill author

Files: evaluation/improve.py, evaluation/program.md, evaluation/tests/test_improve.py.
Consumes control.jsonl in each episode; optional regression and incumbent evidence.

- [x] Add failing tests for importing control evidence and rejecting held-out regression input.
- [x] Extend prepare_evidence with optional regression episode and selected incumbent snapshot,
  preserving tested skills separately. Validate sources before copying.
- [x] Revise instructions to produce short reusable operating guidance with evidence,
  uncertainty, checks and bounded recovery; interpret actual supplied tool documentation.
- [x] Preserve defer behavior for infrastructure errors, fixtures and insufficient evidence.
- [x] Test evidence integrity, incumbent/tested distinction and candidate length validation.

## Task 3: Integrated coordinator and frozen evaluation

Files: evaluation/loop.py, evaluation/evaluate.py only if required,
evaluation/tests/test_loop.py, new evaluation/transfer.py and tests if clearer.

- [x] Add failing tests that rejected candidate evidence cannot replace incumbent skill input.
- [x] Wire selected incumbent and development success evidence into the improver.
- [x] Record updated control contract in frozen identity and expected executor metadata.
- [x] Add frozen evaluation command using selected snapshot, explicit states/tasks,
  fresh episodes and complete reports; never invoke improvement or promotion.
- [x] Test development/held-out boundary, snapshot mutation, config mismatch and no promotion.

## Task 4: Integration, verification and reproducibility

Files: docs/SETUP.md, execute/README.md, README.md, docs/IMPLEMENTATION.md.

- [x] Run all offline tests and lint; run real simulator pose checks and fresh-process loop episodes.
- [x] Run a bounded live acting/evidence/improvement integration with standard speed.
- [x] Inspect result/actions/video artifacts and distinguish software correctness from task success.
- [x] Review complete diff, resolve substantive findings and document reproducible commands,
  limits, measured results and remaining unverified behavior.

## Progress

- Baseline: 88 tests passed in isolated WSL worktree at 3b14746.
- Plan approved by the user's request to implement the previously agreed architecture.

- Control review: no blocking defect; added rot6d interpolation-jump rejection and
  an end-to-end stock-policy mock-HTTP + real physics rotation/gripper test.
- Whole-branch review found frozen profile budget-tamper gap; coordinator added
  original profile consistency checks and regression tests; scoped review found no blocker.
- Live author run cost $0.2055375 and proposed an unvalidated task-specific recipe.
  Observed content errors are now regression cases for stronger proposal checks.
- User delegated demo externally and set hackathon deadline under three hours;
  viewer integration stays in demo/ and docs/DEMO.md owned by that agent.
- Latest full suite: 151 passed; Ruff clean. Acting model is Fable 5.1 as requested.
- Fable's response allowance increased to 8192 after a 1024-token probe truncated.
  HTTP contract tests verify the effective limit and preservation of image history.
- First full Fable loop preserved two refusal errors and an author timeout; no
  candidate was tested. Fresh follow-up uses concise output and a Sonnet author.
- Follow-up completed all six episodes and one author pass: 0/3 versus 0/3,
  two policy errors per condition, candidate rejected, no guide promoted.
  Sonnet author cost $0.13599505. No learned gain or held-out transfer established.
- Calibrated RGB-D now supplies measured visible-surface positions through
  `locate_pixels`. It reduces monocular geometry ambiguity but supplies no object labels,
  centers, grasp targets or task-success signal. Camera calibration is synchronized to
  each observation, including wrist motion.
- The public reactive controller cancels queued waypoints after `stalled` or
  `motion_limit` feedback and requests a fresh policy action; ordinary chunks continue.
- A fresh matched three-state cream-cheese baseline/candidate confirmation is running.
  It is not evidence of task success or learning until both complete and pass the frozen
  provenance and comparison checks.
