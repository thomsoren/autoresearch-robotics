# Integrated robot operating-guide experiment

Worktree: `/home/ludvig/autoresearch-architecture` in WSL Ubuntu-24.04.
Branch: `ludvig/skill-architecture`, based on diagnostics commit `3b14746`.
The original `/home/ludvig/autoresearch-robotics` checkout is preserved.

## Run

The prepared worktree reuses the original ignored virtual environment, LIBERO
vendor checkout and .env through symlinks. No key appears in prompts or tracked files.
A separate installation follows the existing Linux/macOS locked setup in SETUP.md.
Use `PYTHONPATH=.` because this repository is not installed as a Python package.

```bash
cd ~/autoresearch-architecture
PYTHONPATH=. .venv/bin/python -m execute.inspect_agent --smoke --max-steps 80
PYTHONPATH=. .venv/bin/python -m evaluation.loop --smoke --max-steps 30 \
  --episode-timeout 60 --max-seconds 400 --output runs/loop-smoke-new
```

Run the actual two-agent loop with bounded development trials:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation.loop \
  --model claude-fable-5-1 --control pose --iterations 1 --max-steps 600 --max-calls 10 \
  --episode-timeout 300 --max-seconds 2100 \
  --improve-model claude-sonnet-4-6 --improve-budget-usd 0.75 \
  --improve-turns 14 --improve-timeout 300 --output runs/learning-new
```

This runs states 0,1,2 for baseline and each candidate. Requests use standard
speed. The example bounds executor HTTP attempts to 60 and skill-author spending
through the SDK to $0.75, but does NOT impose a dollar ceiling on executor calls.
Output folders must be new. Increase `--iterations` for additional bounded revisions.

The selected guide stays in an immutable run snapshot. Inspect `loop.json` for
`selected_skill`, `selected_batch`, actual status and iteration decisions. No active
skill is silently overwritten. To continue from an accepted guide, pass its path
with `--skill`; this starts a new experiment with a fresh baseline.

## Frozen evaluation

Only after selecting and freezing a guide, run a separate report:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation.transfer \
  --loop runs/learning-new --output runs/heldout-new \
  --suite libero_goal --task-id 0 --states 3 4 5 6 7
```

This command is provided for later use; held-out states are not consumed during
implementation. It verifies original code, prompt, skill hashes and execution
budgets, then runs original baseline and selected guide with fresh sessions.
Changing source code after development selection intentionally invalidates this
comparison; collect a new development baseline after engineering changes.

For cross-task transfer, explicitly select a separately reserved suite/task and
its starting states. Frozen episodes are marked `evaluation_split: held_out`
even when their numeric state IDs are 0,1,2. The improver and development comparer
reject those artifacts. Frozen evaluation never invokes improvement or selection.
Results describe these selected tasks, not general robotics competence.

## Components and evidence

- `execute/inspect_agent.py`: stock acting policy and pose/legacy XYZ adapters.
- `execute/control.py`: bounded OSC feedback and rot6d validation; no scene geometry.
- `simulation/sim.py`: actual physics, site-consistent pose, cameras, LIBERO outcome.
- `evaluation/improve.py` and `program.md`: read-only evidence analysis and one
  proposed general operating guide, with syntax checks against coordinate recipes.
- `evaluation/loop.py`: immutable snapshots, complete development batches,
  selected-incumbent/regression evidence and strict success-count selection.
- `evaluation/transfer.py`: separate frozen evaluation reports.

`control.jsonl` maps every servo waypoint to start/end physics steps and measured
residuals. `inspect_chunk_final` marks the end of a tool motion. Native Inspect
waypoint counts/durations are NOT actual physics-step counts or wall latency.
The videos omit LLM thinking time. No hidden model reasoning is needed for learning.

The proposed guide's fixed role is robot operation across tasks: interpreting
observations, choosing bounded actions, verifying progress and recovering.
Text checks reject observed coordinate/code-recipe patterns; they cannot prove
semantic generality. Fresh reserved-task evaluation remains necessary.

## Current evidence and limits

`runs/architecture-live-baseline` is a genuine live Opus attempt: 240 physics
steps, two HTTP attempts, success false, no policy error. Its video, camera stills,
commands and control records are preserved.

`runs/architecture-live-improvement` is a genuine Sonnet author attempt, $0.2055375
recorded SDK cost. It proposed an unvalidated scene-specific guide; manual review
found memorized coordinates and confused waypoint accounting. That guide is NOT
accepted or promoted. Its errors motivated stricter author instructions/checks.

Real physics tests cover corrected centimetre motion, consistent quaternion frame,
rotation with position hold, episode limits and stock Inspect pose/gripper tools
using a mocked HTTP response. A mocked-response test is not an LLM success.
Task success and learned gains must be read from actual complete comparisons.
Gripper-tip testing, VLA training and a new agent framework are excluded.

## Verification

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Latest full verification: **130 tests passed**, Ruff clean. The stock pose/gripper
integration test uses mocked HTTP responses with real LIBERO physics.

Upstream torch/robosuite deprecation warnings remain. The existing diagnosis scripts
are historical scientific evidence, distinct from evaluated robot-agent episodes.
See [demo handoff](DEMO-AGENT-PROMPT.md) for artifact-viewer integration.

The user selected Fable 5.1 (`claude-fable-5-1`) for all new acting runs.
The earlier Opus development loop was interrupted; it is not a Fable baseline.
The skill-author model remains independently configurable.
Fable retains image history and receives an 8192-token response allowance, including
thinking. The initial 1024-token probe truncated before any action; the corrected
probe in `runs/architecture-fable-check-8192` ran 120 physics steps without a policy
error (task success false). New baseline/candidate runs share the corrected setting.

`runs/architecture-fable-learning` finished its baseline at 0/3 successes with
two policy errors (Fable refusals). The author used the clean failed episode but
timed out at 180 seconds before producing valid structured output. The loop is
marked `error`; no candidate was tested or accepted. The follow-up run is
`runs/architecture-fable-learning-2`, using brief operational-note guidance and
a concise Sonnet author pass. Its own artifacts determine its outcome.
