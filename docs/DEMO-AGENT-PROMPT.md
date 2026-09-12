# Prompt for the independent demo agent

Build the hackathon demo for autoresearch-robotics. Deadline is under three hours.
Build a small working artifact viewer, not another robot/agent framework.

## Context

A fixed LLM acts on calibrated RGB-D images and measured robot pose in LIBERO.
It may query selected displayed pixels for metric visible-surface coordinates using
the synchronized depth, intrinsics and current camera transform. This sensor advantage
reduces monocular scale and coordinate ambiguity; it does not provide object labels,
object centers, grasp targets or task ground truth. Between episodes, a separate LLM
diagnoses execution evidence and proposes reusable Markdown operating knowledge. Fresh
development runs test the candidate; only strictly higher LIBERO success counts retain
it. Skills describe perception, action selection, verification and bounded recovery,
never memorized trajectories. ASPIRE inspires this; our current ablation changes
Markdown, holding execution fixed.

The acting model is Fable 5.1 (`claude-fable-5-1`) using the existing .env token.
The skill-author model is configured separately. Do not conflate older Opus runs
with Fable results. The completed Fable loop is `runs/architecture-fable-learning-2`.
It ran baseline, author, candidate tests and rejection: 0/3 successes in both
conditions, two policy errors in each, no selected guide. Baseline/candidate
physics steps total 919/1140; API attempts total 5/11. Sonnet author cost $0.13599505.
Use this as the primary saved replay. It proves the workflow, not a learning gain.
The preceding `architecture-fable-learning` run ended on an author timeout;
it is preserved as failed-run evidence and has no tested candidate.

The implementation includes measured translation/orientation control, per-waypoint
evidence, operational schema wording, bounded `locate_pixels` queries, an evidence-only
guide author, immutable snapshots, development comparisons and a separate frozen
evaluation path. A public Inspect controller discards the remainder of a motion chunk
after measured `stalled` or `motion_limit` feedback, then requests a fresh action from
the new observation; successful chunks continue normally.

Current task: LIBERO Goal task 0, open the middle drawer. Development states
0,1,2 only. DO NOT run states 3-7 or any model/API call. Gripper-tip work is excluded.

Verified: 151 tests pass, including real physics control and camera-geometry checks. The complete
Fable cycle generated a general guide and rejected it after fresh tests. Older
Opus/Sonnet experiments remain available but are not part of that comparison.
No successful learned improvement is established.

A fresh matched three-state cream-cheese baseline/candidate confirmation is running
under `runs/cheese-confirmation`. Do not present it as a completed comparison until all
six episodes finish, the parts are assembled, and frozen provenance validation passes.
Calibrated depth or visible surface localization alone is not a learned-success result.

## Workspace and ownership

Windows host; prepared runtime is WSL Ubuntu-24.04.
Integration worktree: `/home/ludvig/autoresearch-architecture`.
Integration branch: `ludvig/skill-architecture`.
Original checkout: `/home/ludvig/autoresearch-robotics`.

Use a separate worktree/branch. Own ONLY `demo/` and optionally `docs/DEMO.md`.
Do not reset, checkout, stash, clean, or edit the integration agent's worktree.
Do not modify execute/, simulation/, evaluation/, dependencies, or other docs.
Read existing run artifacts only. Never open .env or spend API credits.

## Viewer

Create one legible screen for a 90-second demonstration, with details on click:

- Task and loop stages: Run, Diagnose, Revise, Test, Keep/Reject.
- Baseline/candidate video or stills with explicit condition labels.
- Tested and candidate guide text with a readable diff.
- Diagnosis, evidence citations, uncertainty and predicted behavior.
- Exact task successes, physics steps and API counts where recorded.
- Selection decisions, including rejection/defer/inconclusive.

Poll for new files so it can show an ongoing loop. Support saved replay for
presentation reliability. Partial/missing JSON must not crash the viewer.
Do not imply video synchronization unless a shared timing basis exists.

Prefer Python standard-library HTTP server plus plain HTML/CSS/JavaScript:

```bash
python -m demo.serve --run /absolute/path/to/run --port 8765
```

Bind localhost. Serve only viewer assets and selected-run artifacts. Reject path
traversal and symlink escapes; never expose repository files or credentials.

## Artifact contract

Inspect actual field names. A genuine standalone example is:
`/home/ludvig/autoresearch-architecture/runs/architecture-live-baseline`.

Standalone layout:

```text
experiment.json
executor-trace.json
requests.jsonl
state-000/result.json
state-000/actions.jsonl
state-000/control.jsonl
state-000/episode.mp4
state-000/<step>-agentview.png
state-000/<step>-robot0_eye_in_hand.png
state-000/<step>-agentview-depth.npz
state-000/<step>-robot0_eye_in_hand-depth.npz
state-000/<step>-camera-geometry.json
```

Loop layout:

```text
loop.json
protocol.json
baseline/manifest.json
baseline/summary.json
baseline/state-000/...
baseline/raw/state-000/experiment.json
baseline/raw/state-000/requests.jsonl
iteration-001/improvement/improvement.json
iteration-001/improvement/evidence.json
iteration-001/improvement/skills/<name>/SKILL.md
iteration-001/candidate/summary.json
iteration-001/candidate/state-000/...
iteration-001/comparison.json
```

Control records contain start_step/end_step, requested/achieved_position,
requested/achieved_rotation, position_error, rotation_error, reached, stop_reason,
gripper_command and inspect_chunk_final. They describe intermediate waypoints.
`reached` is NOT task success. Native Inspect steps count waypoints, while our
result.json counts actual physics. Video omits LLM thinking time.
`locate_pixels` returns measured visible surfaces in world meters for at most eight
integer image pixels. It does not identify objects or verify centers, contacts, grasps
or goals; do not visualize its output as any of those claims.

Only `result.json.success` establishes benchmark outcome. Display missing metrics
as not measured. Label any synthetic fixture visibly; never mix it with genuine
results. A rejected candidate still demonstrates the actual loop, not improvement.
Use `result.json.api_requests_attempted` for request counts, including retries;
`requests.jsonl` can omit transport exceptions and therefore undercount attempts.
Infer tested/rejected status from comparison.json, while preserving the original
proposal's immutable `validation_status: unvalidated` field.

Deliver working demo files, verified saved-run screenshot/browser evidence,
a launch command, a 90-second walkthrough, your commit hash, and exact integration
instructions. Document assumed fields in docs/DEMO.md.
