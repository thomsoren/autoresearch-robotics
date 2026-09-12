> For the integrated pose-control and operating-guide branch, use
> [IMPLEMENTATION.md](IMPLEMENTATION.md) and [execute/README.md](../execute/README.md).
> Earlier experiment descriptions below are retained as setup history.

# Autoresearch for robotics

Improve an agent's reusable robot skills through repeated LIBERO evaluations in
MuJoCo. Python + `uv`; no policy training required for the MVP.

The simulator has been smoke-tested on native Apple Silicon macOS (CGL) and
ARM64 Linux in Docker (CPU-only OSMesa). Linux EGL/GPU and x86 Linux are supported
by the setup but have not been runtime-tested here.

## Start here

From the repository root (Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) required):

```bash
uv run --no-project simulation/prepare.py
uv sync --locked
uv run -m simulation.sim tasks --suite libero_goal
uv run -m simulation.sim smoke
```

Preparation downloads the pinned official LIBERO repository, including meshes,
task definitions and initial states, into ignored `.vendor/libero/`. Allow a few
GB for source, environments and caches. Demonstration datasets are not needed.
Configuration stays in ignored `.libero/`; nothing changes `~/.libero`.

The smoke command saves `runs/smoke-*/episode.mp4`, camera PNGs, actions and
`result.json`. It moves upward briefly; `success: false` is expected because it
does not solve the task. Each command creates a fresh output directory to avoid
overwriting evidence. Use `--output runs/my-run` to choose its name.

Verified locally: two-camera rendering and MP4 output on both platforms; 20
control steps moved the gripper about 2.4 cm on each. The macOS motion-tool check
reached an XYZ waypoint within 1 cm and closed/reopened the gripper. Two fixed-state
no-op evaluations completed with zero policy errors. Simulator and evaluation tests pass.
No agent has solved a task yet; these are infrastructure checks.

```bash
# Repeated fixed-state infrastructure evaluation (not an agent baseline)
uv run -m evaluation.evaluate --states 0 1 --max-steps 20 --output runs/noop
uv run --locked --group evaluation --group execute pytest -q
uv run ruff check .
```

## Lean layout

```text
simulation/     Thomas: preparation, robot API and simulator tests
evaluation/     benchmark runner and improvement-agent work
docs/           setup and agreed design
harness/        reserved for Laksiya; not created by simulator setup
runs/           generated videos/traces/results; ignored
```

## Earlier standalone harness integration proposal

The current MVP uses the existing `execute/` executor; see [HANDOFF.md](HANDOFF.md)
for current team assignments. This earlier standalone integration remains optional.
Import `Robot` with
`from simulation.sim import Robot`. The following is the earlier optional
integration proposal for the preserved evaluation utility; adapt it to her
harness design. A synchronous `run_episode(robot, skill_dir)` entry point can
be added in `harness/agent.py`.
That function starts a **fresh agent session for each episode**, loads only the
provided skill snapshot if present, and serially exposes these methods as SDK
tools. Keep the same `Robot` alive for the whole episode:

| Method | Purpose |
| --- | --- |
| `robot.observe()` | Two RGB image paths, gripper pose, remaining steps, success |
| `robot.move_to([x,y,z], max_steps=60)` | Feedback-controlled world XYZ in meters; returns `reached` |
| `robot.gripper(closed=True, steps=15)` | Close/open for a bounded number of steps |
| `robot.step([dx,dy,dz,rx,ry,rz,grip], repeat=1)` | Normalized OSC delta action, each value in `[-1,1]` |

Images must be delivered to the model as **image content**, not just filenames.
`move_to` maintains the current orientation; it is not a collision-free planner.
Rotation is available via `step`. A translation unit is 5 cm; a rotation unit is
0.5 rad; grip `-1` opens and `+1` closes. Every repeat consumes one of the 500
default control steps (20 Hz). Commands stop on success or budget exhaustion.
Check `reached` and new images after moving; use intermediate waypoints.

The evaluator owns reset, initial-state selection and scoring. Do not expose
reset, internal simulator access, arbitrary evaluator edits or score setters to
the acting agent. The adapter is a collaboration boundary, not a security sandbox.
Run all simulator calls serially on the **same thread** that constructs `Robot`.
If using an async SDK, call tools directly on its event-loop thread; avoid
`asyncio.to_thread` for OpenGL calls. The synchronous entry point may use
`asyncio.run(...)`. Add the SDK with `uv add claude-agent-sdk` when implementing it.

```bash
# If harness/agent.py uses this entry point; same agent/model/tools/budgets in both conditions
uv run -m evaluation.evaluate --policy harness.agent:run_episode --states 0 1 2 --output runs/baseline
uv run -m evaluation.evaluate --policy harness.agent:run_episode --states 0 1 2 --skills skills --output runs/candidate
```

Return values from the policy cannot declare success. The evaluator reads
LIBERO's task predicate, counts policy exceptions as failures, and writes
`manifest.json`, `results.jsonl`, `summary.json`, per-episode videos and action
traces. Each run snapshots and hashes its input skills. Setup errors abort the
run instead of becoming robot failures. Existing output folders are rejected.

Laksiya's adapter must also enforce model-call, token/cost and wall-time limits,
and record model ID, SDK version, prompts, tool calls and API usage. Only physics
steps are bounded by the existing evaluation utility; it cannot interrupt a hung API call.
For the no-skills condition, disable SDK auto-discovery/memory of project skills.
For the skills condition, explicitly load the provided snapshot, not the live
directory. The separate improvement SDK and completed-run comparison are implemented
below; automatic candidate replay and updating active skills remain future work.

Default observations are RGB plus robot proprioception. `--privileged` also
exposes object poses for initial debugging. Label those runs separately; they are
not comparable to vision-only benchmark results. This harness measures a small
LIBERO task subset under an agent protocol, not the published full LIBERO protocol.

## Improvement agent

`evaluation/improve.py` uses the official
[Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python).
Its optional dependency group keeps the simulator's installation independent:

```bash
uv sync --locked --group evaluation
```

Set `CLAUDE_API_KEY` in the repository's ignored `.env`, or the environment.
The environment takes precedence. The runtime maps this to `ANTHROPIC_API_KEY`
only for its SDK subprocess, using the direct Anthropic endpoint. It uses a
temporary working/config directory, disables automatic project settings and
skill discovery, and exposes only two read-only evidence tools. It does not use
your interactive Claude login or modify the executor's credential setup.

```bash
# No API call: package an existing completed episode and list missing evidence.
uv run --group evaluation -m evaluation.improve runs/eval-smoke/state-000 --prepare-only

# API smoke: diagnose the saved no-op episode; a skill proposal is prohibited.
uv run --group evaluation -m evaluation.improve runs/eval-smoke/state-000 --kind smoke

# Once a real executor episode exists, supply its raw trace and frozen input skills.
# Omit --skills for a no-skills baseline; omit --trace if no export exists yet.
uv run --group evaluation -m evaluation.improve runs/baseline/state-000 \
  --kind agent --trace runs/baseline/executor-trace.jsonl \
  --skills runs/baseline/skills --model opus
```

The example run paths are local artifacts, not included in Git. To create a fresh
no-op fixture, run `uv run -m evaluation.evaluate --states 0 --max-steps 10
--output runs/noop`, then supply `runs/noop/state-000` to `improve`.
Use a trace containing only the selected development episode, not a mixed batch
or held-out export. This first importer preserves trace text as supplied; it
does not assume Laksiya's export schema or invent missing tool events.

Defaults: `--model sonnet`, `--max-turns 8`, `--max-budget-usd 0.5`, and
`--timeout 120` seconds. The SDK checks its dollar limit after model calls, so
it is not a hard billing ceiling. Model selection is independent of the executor.
Every invocation starts a fresh session and requires a new output directory.

`runs/improve-*/` contains the hashed evidence snapshot, copied `program.md`,
agent messages/tool requests in `agent.jsonl`, SDK model/usage/cost in `usage.json`,
and `improvement.json`. A proposal adds `skills/<name>/SKILL.md`; it is marked
unvalidated and does not update active skills. Errors produce `error.json`;
usage is available when the SDK returns a terminal result. Images and requested
MP4 frames are delivered as image content. The importer rejects states outside
0–2, and validation prohibits proposals from smoke/no-op or errored episodes.

Local verification: 59 tests pass, and the saved no-op video and both camera
stills decode through the evidence tools. Live SDK authentication and structured
output passed with `--model claude-sonnet-4-6`: the agent read metadata/actions,
viewed video and a camera still, and returned `defer` without a candidate.
The smoke cost $0.0409111; local evidence is in `runs/improvement-auth-check/`.
This verifies the SDK pipeline, not the factual accuracy of every diagnosis or
any improvement in robot performance.

The subsequent prompt check in `runs/improvement-provenance-check/` correctly
identified the open-gripper command, deferred a reward prediction, and kept
executor skill loading unknown. Reports remain unvalidated hypotheses: zero
commanded motion alone does not prove measured stationarity, for example.

## Compare skills

Before collecting baseline/candidate runs, create one frozen protocol JSON under
`runs/`, filled with the real executor settings. These are declarations for
comparison, not commands to change Laksiya's executor:

```json
{
  "model": "EXACT_EXECUTOR_MODEL_ID",
  "base_prompt_sha256": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS",
  "executor_revision": "EXACT_EXECUTOR_CODE_REVISION",
  "executor_config_sha256": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS",
  "max_turns": 40,
  "max_budget_usd": 1.0,
  "timeout_seconds": 300,
  "fresh_session_per_episode": true
}
```

Hash the fixed base prompt and the actual nonsecret executor configuration
(model options, tool/observation and skill-loading settings), excluding candidate
skill contents/paths and per-run output paths. Use a pinned model ID and code
revision covering executor/tool dependencies; do not record API keys. The harness
must enforce these values and start fresh sessions; this runner still bounds
only physics steps. Use the same protocol file in both conditions.

```bash
# Once Laksiya supplies the entry point; these commands run the real executor.
uv run -m evaluation.evaluate --policy harness.agent:run_episode \
  --states 0 1 2 --protocol runs/protocol.json --output runs/baseline
uv run -m evaluation.evaluate --policy harness.agent:run_episode \
  --states 0 1 2 --protocol runs/protocol.json --skills runs/proposal/skills \
  --output runs/candidate

# No model or simulator calls: validate and compare completed runs.
uv run -m evaluation.evaluate --compare runs/baseline runs/candidate
```

Default output: `runs/candidate/comparison.json`; use `--output FILE.json` to
choose a new file. Existing files are rejected. The decision is `keep` only for
a strictly higher development success count and a changed Markdown snapshot;
ties/regressions select the baseline. Policy/API errors count as failed attempts.
Missing protocol, mismatched model/configuration/budgets/revisions, no-op fixtures
or a missing/non-Markdown candidate produce `inconclusive`. Corrupted/incomplete
batches, changed snapshots and state lists other than [0, 1, 2] fail validation.
Original evidence is untouched. An inconclusive comparison selects the incumbent;
no decision updates the active `skills/` directory. Use `selected_skill_dir` for
the next run (null means no skills). Freeze the final selection before evaluating
held-out states 3–7; those states cannot enter this selection command.

## Inspect Robots reports

The optional `evaluation` dependency group includes `inspect-robots`. Reporting
reads our recorded results; it does not use Inspect Robots' rollout runner or
agent plugin. It makes no API calls and leaves the executor unchanged.

```bash
uv sync --locked --group evaluation

# Report an existing completed batch; no simulator or executor run.
uv run --group evaluation -m evaluation.evaluate --report-existing runs/noop

# Or add a report to a new evaluation.
uv run --group evaluation -m evaluation.evaluate --states 0 1 --max-steps 10 \
  --output runs/reported-noop --report
```

Open `<run>/inspect/log.html`. Its accompanying `log.json` uses Inspect Robots'
schema and preserves the original success rate, counts, policy errors and skill
hash. `libero_success` is the task score; the log/scene `status` describes whether
execution completed or errored. Policy/API errors remain zero-score attempts in
the denominator, unlike Inspect Robots' default runner aggregation.

The HTML includes an explicitly labelled appendix of original MP4 videos and
step-labelled camera PNGs, with a 20 MB source-media budget. Omitted files remain
in the original episode directories. We do not synthesize executor transcripts
from low-level actions or claim these images were seen by the acting agent.
Our appendix is added after the stock renderer; independently rendering the JSON
with `inspect-robots view` shows the standard results without this appendix.

The exporter rejects incomplete/inconsistent batches and an existing `inspect/`
output directory. Original artifacts remain unchanged. Rendering failures leave
the completed benchmark files intact. Verified on native macOS: two 10-step
no-op episodes and a report; automated tests cover error denominators, embedded
images and source preservation. This report is an infrastructure check, not an
agent-performance result.

## Linux and Mac fallback

On Ubuntu 22.04/24.04, install renderer libraries before the same uv commands:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential linux-libc-dev libglib2.0-0 libegl1 libgl1 libglfw3 libosmesa6
uv run --no-project simulation/prepare.py
uv sync --locked

# NVIDIA/Linux with working EGL drivers:
MUJOCO_GL=egl uv run -m simulation.sim smoke

# CPU-only Linux, including a Linux VM on Mac:
MUJOCO_GL=osmesa uv run -m simulation.sim smoke
```

No NVIDIA GPU is needed for this CPU simulation + remote model API MVP. Linux
uses CPU PyTorch wheels; Torch only loads LIBERO's bundled initial states.
On macOS, `sim.py` selects CGL offscreen rendering automatically. No GUI viewer
or `mjpython` is needed for these offscreen commands. Keep a Linux VM/remote
machine as the fallback if CGL fails on another Mac; do not spend the hackathon
debugging a GUI viewer. Move the code/lockfile, then rerun preparation and sync;
do not copy `.venv` or `.libero` across machines.

There is also a CPU-only Docker fallback (Docker Desktop on Mac, Docker Engine
on Linux). It downloads dependencies and assets inside the image:

```bash
docker build -f simulation/Dockerfile -t autoresearch-robotics:local .
mkdir -p runs
docker run --rm -v "$PWD/runs:/app/runs" autoresearch-robotics:local
```

This image contains the simulator and evaluator. Rebuild it after simulator
changes. Native `uv` is the simpler development path for adding Laksiya's agent.

Versions of MuJoCo, robosuite, NumPy and Torch are deliberately pinned for this
older LIBERO API. In particular, do not independently upgrade robosuite to 1.5.
The local source-path workaround in `simulation.prepare.configure()` handles upstream's
namespace packaging under modern setuptools without patching upstream files.

See [DESIGN.md](DESIGN.md) for the agreed architecture and evidence contract,
and [evaluation/program.md](../evaluation/program.md) for improvement-agent instructions.

## Automatic Inspect skill loop

`evaluation/loop.py` coordinates the separate `execute/` experiment. It runs fresh
executor processes on development states **0, 1, 2**, asks the Claude Agent SDK
improvement agent to revise one task skill from a failed episode, then compares a
complete candidate batch against the selected incumbent. The acting model,
controller and simulator remain fixed. The improvement agent can inspect frames,
actions, the executor transcript, the tested skill and prior selection decisions.

```bash
# Three real simulation fixtures; no API requests or skill selection.
uv run --locked --group evaluation --group execute -m evaluation.loop \
  --smoke --output runs/loop-smoke

# Paid loop: fresh no-skill baseline, then at most three candidate revisions.
uv run --locked --group evaluation --group execute -m evaluation.loop \
  --output runs/drawer-loop --iterations 3 --max-seconds 3600
```

Both agents use the existing root `.env` `CLAUDE_API_KEY`; no key is passed in
command arguments or evidence. Both default to **Opus 5 at standard speed**.
`--skill path/to/SKILL.md` optionally supplies the initial incumbent. Use a fresh
output directory each time; automatic resume is not implemented.

Defaults per executor episode: 12 HTTP attempts including retries, 300 physics
steps, and 180 seconds inside the executor. The coordinator allows 30 seconds
of additional process startup/report cleanup and kills the subprocess group on
hard timeout. Per improvement: 18 SDK turns, requested $1 SDK budget, 240 seconds
plus the same process allowance. The overall one-hour deadline prevents starting
phases that cannot fit their full allowance; it never shortens one condition's
budget to fit the remaining time. Ctrl-C stops the active subprocess group.

**The executor has no dollar cap.** Three revisions mean at most 12 episodes /
144 executor HTTP attempts, plus three improvement sessions with $1 requested
budgets each. SDK budgets can be exceeded by an in-flight request. Use
`--iterations`, `--max-calls`, `--max-steps`, `--episode-timeout`,
`--improve-budget-usd`, `--improve-turns` and `--improve-timeout` to bound the run.

```text
runs/drawer-loop/
  protocol.json                  frozen code/configuration and base prompt
  loop.json                      current status and selected frozen skill path
  baseline/                      manifest, summary, results, skills if supplied
    state-000/                   copied episode evidence and executor trace
    state-001/
    state-002/
    raw/state-000/inspect/html/   original native Inspect report (one per state)
  iteration-001/
    context.json                 selected incumbent and prior decisions
    improvement/                 SDK evidence, report, usage and candidate skill
    candidate/                   same batch layout as baseline
    comparison.json              deterministic keep/reject/inconclusive result
```

`loop.json.selected_skill` identifies the best tested frozen `SKILL.md`, or is
null when the no-skill baseline remains selected. A candidate is selected only
on a strictly higher LIBERO success count; ties and regressions retain the
incumbent. Rejected failures still inform the next revision. No root `skills/`
file is overwritten. Starting images, derived simulator seeds, source hashes,
full skill presence in the acting prompt and complete batches are checked.
Original native exports are retained, including their actual derived seeds.

API/policy errors with valid exports count as failed attempts. Setup failures,
missing exports, changed evidence/configuration, invalid proposals and hard
subprocess timeouts stop the loop without selecting an incomplete candidate.
A deferred proposal or a batch with no ordinary failed episodes stops diagnosis.
The latest checkpoint and partial artifacts remain inspectable.

This is development selection, not proof of generalization: states **3–7 are
never used by the loop**. Held-out evaluation and active-skill publication remain
separate. Structural validation cannot guarantee that generated skill advice is
correct, or that the executor follows it. Fewer stalled steps and closing the
gripper are diagnostic signals; LIBERO success determines selection.
