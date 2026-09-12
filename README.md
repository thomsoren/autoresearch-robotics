# Autoresearch for robotics

Agent skill improvement on LIBERO in MuJoCo. Python 3.11, managed with `uv`.

## Ownership and layout

```text
simulation/     Thomas: MuJoCo/LIBERO setup, robot tools, existing evaluation utility and tests
docs/           setup guide, project plan and earlier skill-loop notes
harness/        reserved for Laksiya; she creates and owns this folder
pyproject.toml  shared uv environment
uv.lock         pinned dependencies
```

Simulator work stays in `simulation/`. Laksiya owns the agent SDK, harness and
skill-improvement loop. The existing evaluation utility is preserved under
`simulation/evaluate.py` as a reference; it does not dictate her harness design.

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
See [setup and robot API](docs/SETUP.md), [10-hour plan](docs/PLAN.md), and the
[earlier loop proposal](docs/program.md). These notes are a handoff for Laksiya,
not an implemented agent harness.

```bash
uv run pytest simulation/tests -q
docker build -f simulation/Dockerfile -t autoresearch-robotics:local .
mkdir -p runs
docker run --rm -v "$PWD/runs:/app/runs" autoresearch-robotics:local
```
