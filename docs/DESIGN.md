> Historical MVP design. The integrated reusable operating-guide design is in
> [ARCHITECTURE.md](ARCHITECTURE.md), with current commands and evidence in
> [IMPLEMENTATION.md](IMPLEMENTATION.md).

# Robotics skill improvement: agreed design

## Objective and ownership

A fixed executor becomes more reliable on unseen starting states of the same
LIBERO task because it receives improved Markdown skills. The improvement is in
external skill memory; model weights, architecture, base prompt and robot tools
stay fixed for the MVP. Report no improvement if that is what the results show.

Current assignments are in [HANDOFF.md](HANDOFF.md): Thomas owns simulation and
loop integration, Ludvig owns the existing Inspect executor's robot-control work,
and Laksiya owns improvement-agent quality. The earlier `harness/` proposal stays
separate. The simulator API offers `observe`, `step`, `move_to`, and `gripper`;
the current Inspect experiment exposes `move_by`, `done`, and `give_up`.

| Component | Responsibility | Status |
| --- | --- | --- |
| `simulation/sim.py` | Environment, robot tools, cameras and physics-step limits | Implemented; smoke-tested on Apple Silicon macOS and ARM64 Linux Docker |
| `evaluation/evaluate.py` | Fixed-state episodes, official success, skill snapshots, comparison and Inspect Robots reports | Implemented; selection returns a frozen snapshot, active-skill updates remain separate |
| `evaluation/improve.py` | One SDK agent reads evidence, diagnoses and proposes candidate Markdown | Implemented; live SDK smoke passed with claude-sonnet-4-6, returning defer on a no-op fixture |
| `evaluation/program.md` | Improvement-agent instructions | Written |
| Laksiya's executor | Fresh acting session and tool loop per episode | Independently owned; no executor code is included in this checkout |

Keep evaluation to two Python modules initially. Add plain functions before
classes or new modules. No separate judge, diagnosis, optimizer, registry,
memory service or configuration framework. Share root `pyproject.toml`, `uv.lock`
and ignored `.env`. `CLAUDE_API_KEY` is mapped to the SDK's `ANTHROPIC_API_KEY`
in the improvement subprocess only; credentials never belong in prompts.

The automated Inspect experiment now adds one coordinator, `evaluation/loop.py`.
It runs complete fixed-state batches through the existing `execute/` entry point,
packages native exports for `compare_runs`, and invokes `improve.py` on a failed
development episode. Rejected candidates inform subsequent diagnosis while the
selected incumbent remains unchanged. This experimental path does not modify
Laksiya's harness. Commands, limits and generated layout are in
[SETUP.md](SETUP.md#automatic-inspect-skill-loop).

## Skill format and lifecycle

Start with `skills/open_drawer/SKILL.md`: a short name/description in YAML
frontmatter, followed by when to use, preconditions, procedure, progress checks
and bounded recovery. Extract shared behavior skills when repeated evidence
establishes reuse. The active `skills/` directory is created with its first real
accepted skill, not an empty template or an unvalidated learned procedure.

Expose skill names/descriptions and load selected bodies on demand. Log which
version was loaded when that evidence is available from the executor. Fixed
tool documentation owns syntax, units and action limits; learned skills own
the conditions and procedures for using tools successfully.

The improvement agent reads development evidence and an incumbent snapshot,
then writes a separate candidate. Only promotion updates active skills:

```text
runs/<experiment-id>/
  baseline/             baseline evaluations and episode artifacts
  candidate/            candidate evaluations and episode artifacts
  skills/               frozen proposed Markdown
  improvement.json      evidence, hypothesis, predicted change
  comparison.json       measured comparison and promotion decision
```

This experiment wrapper is the target organization; the current runner writes
one batch at the supplied `--output` path. It already preserves per-episode
metadata, actions, camera output and results, plus a batch manifest, results
JSONL and summary. Generated artifacts are ignored by Git. Do not replace
evidence with a growing changelog in the executor's skill instructions.

## Available evidence and its limits

Audited source and local smoke/no-op/motion-check artifacts. Those runs validate
infrastructure; they are not agent failures from which to learn a task skill.

| Evidence | Source | Limitation |
| --- | --- | --- |
| Task, starting state, seed, budget and LIBERO revision | `episode.json`, `result.json` | Model/prompt identity and API budget are not currently recorded |
| Outcome, termination, policy errors | `result.json` and batch results | Failure alone does not establish a cause; setup errors abort a batch |
| Low-level actions, reward, success by physics step | `actions.jsonl` | Does not identify the parent high-level tool call |
| External-camera video | `episode.mp4`, 256×256, 4 fps | Contact can be occluded or fall between frames |
| External and wrist stills | Step-prefixed PNGs saved by observations | No continuous wrist video; repeated observations at one step share paths |
| Supplied skill content and hash | Evaluation snapshot and manifest | Does not prove that the executor loaded or followed it |
| Tool names, arguments, returns, errors, loaded-skill events | Laksiya's executor trace, when exported | Actual export format still needs inspection |
| Drawer displacement, contact forces, grasp attachment | Not logged | Cannot claim these as measured facts from current artifacts |

`move_to` returns `reached` and `position_error`; observations return end-effector
pose and finger positions. The simulator saves images, not those complete return
dictionaries. Consume them from the executor trace if available. Privileged mode
exposes selected object poses, not every joint/contact diagnostic automatically.

Physics runs at 20 control steps per simulated second. Video contains the initial
settled frame and every fifth step, plus a terminal frame on success/step limit.
Ten settling steps precede the counted episode. Model thinking time is absent
from the video, so playback duration is not wall time. Early policy return can
leave video ending before the last action: the audited 34-step motion check has
video through step 30 and camera stills at step 34. Inspect the latest still too.

Keep executor call IDs, physics steps and video frame indices distinct. `observe`
advances zero steps and `move_to` may advance many. Mark alignment unknown when
the export cannot establish it; repeated control vectors do not identify a tool.

## Improvement-agent contract

One agent both diagnoses and proposes changes. It receives a read-only package
of development task/results, raw executor traces, indexed videos/stills, exact
incumbent skills and previous decisions. Include successful episodes as regression
evidence and explicitly list missing fields. Preserve original evidence IDs.

Start visual inspection with beginning/end frames and frames near tool failures
or gripper transitions; allow requests for more development frames. The runtime
must deliver images as model image content, not just file paths. Public messages
and tool traces suffice; hidden model reasoning is not required.

The output is evidence references, a hypothesis with uncertainty and alternatives,
a bounded Markdown diff, and a predicted behavior change. Defer editing for an
API outage, simulator failure or insufficient evidence. A closed gripper does
not establish a grasp, and loading a skill does not establish that it was followed.

Example only: a recorded `reached=false` followed by closing the gripper could
motivate checking alignment and making a bounded correction before grasping.
If the return value is missing, the agent cannot assert that observation.

The importer and runtime live together in `improve.py`. The current command
consumes one completed episode, optional raw executor trace and optional frozen
skill directory. It indexes existing evidence without inventing missing events
or requiring extra robot tools. No-op exports already in `runs/` exercise the
pipeline but must produce a deferred diagnosis. A real executor export is still
needed to validate task-skill proposals and adapt any trace parsing. Multi-episode
regression evidence and prior-decision ingestion remain future loop work.

Inspect Robots is used only for its log schema and HTML reporting, through
`export_report()` in `evaluate.py`. Original LIBERO outcomes and all-attempt
denominators are preserved, including policy/API errors. Its default rollout
runner and agent plugin are not used. Original videos/stills appear in a labelled
HTML appendix; executor transcript integration awaits an actual export schema.
This does not change robot tools, skill loading or Laksiya's Claude SDK loop.

## Evaluation and promotion

Start with `libero_goal` task 0, open the middle drawer. If blocked during initial
exploration, task 7, turn on the stove, is a fallback; task 8, bowl onto plate,
is a stretch. Freeze the selected task before baseline collection.

Development states: 0–2. Final held-out states: 3–7. Seed: 0; task order: 0;
default cap: 500 physics control steps. Suggested initial API limits are 40 turns
and five minutes per episode, with a team-chosen cost cap. Validate these against
latency before freezing them; the current runner bounds only physics steps.

Compare identical task/state sets, executor/model configuration, tools, observation
mode and budgets with fresh sessions and frozen skills. Unknown model or
skill-loading provenance weakens the ablation. The deterministic LIBERO predicate
decides success; LLM diagnosis cannot override it. Valid trajectories can differ,
so tool sequences are diagnostic evidence rather than prescribed answers.

Initially promote only on a strictly higher development success count and keep
the incumbent on ties. Require complete comparable runs. Recheck gains when the
budget permits: fixed simulation states do not remove model sampling variability.
Report policy/API errors separately; they count as unsuccessful attempts in the
current evaluator. An infrastructure-aborted batch has no complete comparison.

`compare_runs()` now implements that selection rule for exactly states [0, 1, 2].
It verifies summaries, per-episode records and unchanged skill snapshots. Model,
base prompt, executor revision/configuration and API limits must be declared via
`--protocol` before each evaluation. Simulator/evaluator source hashes and the
dependency lock hash are recorded automatically. Missing or mismatched provenance
returns `inconclusive`; old smoke runs cannot be upgraded by adding protocol
at comparison time. The harness remains responsible for enforcing the declared
settings and fresh sessions. This metadata does not prove a skill was loaded.

The resulting `comparison.json` records counts, errors, hashes, reasons and the
selected frozen skill path. `keep` selects the candidate, `reject` retains the
baseline, and `inconclusive` leaves the baseline selected pending valid evidence.
It never overwrites active skills, reruns an executor or selects using held-out
states. Candidate snapshots must contain Markdown only.

Try at most three candidate revisions. Freeze the chosen skill, then compare it
with the no-skills baseline on untouched states once. Do not provide held-out
results, traces or frames to the improvement agent. Record exact counts, cost
when available, skill diff, and representative successes and failures. Five
held-out states support a hackathon demo, not broad statistical generalization.

## MVP priorities and references

The original ten-hour allocation was: 0–2 h simulator/SDK integration, 2–4 h first
agent attempts and baseline, 4–6 h candidate skill loop, 6–8 h held-out evaluation,
8–10 h demo and recordings. Adjust remaining time to actual progress. If automation
is unfinished, label any human-triggered revision honestly. Skip RL/VLA training,
model architecture search, full-suite evaluation, a dashboard and distributed
simulation until the small loop works. Keep the acting model fixed; a different
skill-author model is acceptable if recorded. Verify model access in the account.

- [Karpathy autoresearch](https://github.com/karpathy/autoresearch): fixed evaluator,
  bounded experiments and keep/reject discipline.
- [VIA](https://github.com/hengyuan-hu/via): visual tools for robot control; its
  selected LIBERO-task results do not guarantee results for our simpler interface.
- [ASPIRE](https://research.nvidia.com/labs/gear/aspire/): execution-feedback-driven
  skill discovery. Acknowledge this prior work; our deliverable is a reproducible
  small loop and an honest ablation.
- [FAEA](https://github.com/robiemusketeer/faea-sim): Claude SDK and state-assisted
  robot control. If using privileged observations, label both conditions that way.
- [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO): task definitions,
  fixed initial states and success predicates.
- The supplied [X post](https://x.com/AGTPinsights/status/2098520186336825571) returned
  403 during research; its exact claim was not verified.
