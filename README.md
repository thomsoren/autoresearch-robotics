# Autoresearch for robotics

Agent skill improvement on LIBERO in MuJoCo. Python 3.11, managed with `uv`.

## Ownership and layout

```text
simulation/     Thomas: MuJoCo/LIBERO setup, robot tools, existing evaluation utility and tests
docs/           setup guide, project plan and earlier skill-loop notes
harness/        Laksiya: Anthropic SDK robot console and skill-improvement loop
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

## Robot console

An Anthropic SDK agent loop that translates terminal instructions into robot tool calls
against one live episode:

```bash
uv run -m harness.repl --task 0 --state 0 --fast
```

Type an instruction (`open the middle drawer of the cabinet`); the agent calls
`observe` / `move_to` / `gripper` / `step` and gets both camera images back each turn.
`/status`, `/budget`, `/usage`, `/timing` and `/quit` are handled locally. Each model
request and each tool call prints its own duration, every instruction ends with a line
splitting its wall clock into model time, simulator time and image-encoding time, and
`/timing` reports those totals for the session (also saved in `session.json`). Every run writes `episode.mp4`,
`actions.jsonl`, `harness_log.jsonl` and `session.json` into its `runs/` directory.
`--fast` enables fast mode (up to 2.5x output speed at premium pricing, `claude-opus-5`
and `claude-opus-4-8` only); `--effort` tunes thinking depth, `--quiet` hides prose and
shows only the tool trace. The agent has no tools beyond the four robot ones, so it is a
clean no-skills condition.

Credentials come from a gitignored `.env` in the repository root, loaded at startup
(`cp .env.example .env`, then fill in `ANTHROPIC_API_KEY`). It is picked up automatically
when present and skipped when absent; variables already exported in your shell win over
the file.

```bash
uv run pytest harness/tests simulation/tests -q
docker build -f simulation/Dockerfile -t autoresearch-robotics:local .
mkdir -p runs
docker run --rm -v "$PWD/runs:/app/runs" autoresearch-robotics:local
```
