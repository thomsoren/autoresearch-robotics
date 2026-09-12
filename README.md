# Autoresearch for robotics

Agent skill improvement on LIBERO in MuJoCo. Python 3.11, managed with `uv`.

## Ownership and layout

```text
simulation/     Thomas: MuJoCo/LIBERO setup, robot tools and tests
evaluation/     benchmark runner, tests and improvement-agent instructions
docs/           setup guide and agreed design
harness/        reserved for Laksiya; she creates and owns this folder
execute/        isolated Inspect Robots agent experiment (Opus 5)
skills/         accepted Markdown skills, created when the first skill is ready
runs/           generated evidence and candidate skills; ignored
pyproject.toml  shared uv environment
uv.lock         pinned dependencies
```

See [team handoff and individual tasks](docs/HANDOFF.md): Ludvig owns robot-control
reliability in `execute/`, Laksiya owns improvement-agent quality, and Thomas owns
simulation and loop integration. Existing `harness/` work stays separate.
Evaluation and improvement work stays in `evaluation/` and consumes executor
artifacts. `evaluate.py` runs benchmarks; `improve.py` uses the Claude Agent SDK
to inspect evidence and propose a Markdown candidate in one agent session.
Development comparison selects the baseline or candidate's frozen skill snapshot.
`evaluation/loop.py` automatically replays the Inspect executor, diagnoses failures,
tests candidate skills on states 0–2 and retains only strict success-count gains.
It records the selected frozen skill path without overwriting active skills.
See [loop commands and budgets](docs/SETUP.md#automatic-inspect-skill-loop).
Inspect Robots provides optional reports from completed batches, including
original videos/stills, without taking over the executor.
The separately requested [Inspect agent experiment](execute/README.md) does use
its stock executor against a limited XYZ/gripper LIBERO adapter.

## Quick start

Run from the repository root:

```bash
uv run --no-project simulation/prepare.py
uv sync --locked
uv run -m simulation.sim tasks
uv run -m simulation.sim smoke
```

Smoke-tested on native Apple Silicon macOS and CPU-only ARM64 Linux in Docker.
Videos, camera images and traces go into ignored `runs/`. The smoke test verifies
movement/rendering; it does not solve a task.

The Python integration entry point is:

```python
from simulation.sim import Robot
```

Keep one `Robot` alive per episode and call it serially on the same thread.
See [setup and robot API](docs/SETUP.md), [agreed design](docs/DESIGN.md), and
[improvement-agent instructions](evaluation/program.md).

```bash
uv run -m evaluation.evaluate --states 0 1 --max-steps 20
uv run --group evaluation -m evaluation.evaluate --report-existing runs/my-completed-batch
uv run --group evaluation pytest -q
docker build -f simulation/Dockerfile -t autoresearch-robotics:local .
mkdir -p runs
docker run --rm -v "$PWD/runs:/app/runs" autoresearch-robotics:local
```

For the separate improvement agent, put `CLAUDE_API_KEY` in ignored `.env`:

```bash
uv sync --locked --group evaluation
uv run --group evaluation -m evaluation.improve runs/eval-smoke/state-000 --prepare-only
uv run --group evaluation -m evaluation.improve runs/eval-smoke/state-000 --kind smoke
```

Use an existing episode directory containing `result.json`; the example is our
local no-op fixture. See [evaluation setup](docs/SETUP.md#improvement-agent)
for real executor episodes, model selection and output files.

After evaluating baseline and candidate with the same recorded executor protocol:

```bash
uv run -m evaluation.evaluate --compare runs/baseline runs/candidate
```

This writes `runs/candidate/comparison.json` with `keep`, `reject` or `inconclusive`.
See [comparison setup](docs/SETUP.md#compare-skills) for the protocol and integrity checks.
