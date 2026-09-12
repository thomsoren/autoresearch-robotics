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
no-op evaluations completed with zero policy errors. Sixteen contract tests pass.
No agent has solved a task yet; these are infrastructure checks.

```bash
# Repeated fixed-state infrastructure evaluation (not an agent baseline)
uv run -m simulation.evaluate --states 0 1 --max-steps 20 --output runs/noop
uv run pytest simulation/tests -q
uv run ruff check .
```

## Lean layout

```text
simulation/     Thomas: preparation, robot API, existing evaluation utility and tests
docs/           setup guide, plan and earlier skill-loop proposal
harness/        reserved for Laksiya; not created by simulator setup
runs/           generated videos/traces/results; ignored
```

## Laksiya: simulation API handoff

Laksiya owns the harness in her own folder. Import `Robot` with
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
uv run -m simulation.evaluate --policy harness.agent:run_episode --states 0 1 2 --output runs/baseline
uv run -m simulation.evaluate --policy harness.agent:run_episode --states 0 1 2 --skills skills --output runs/candidate
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
directory. The SDK and outer improvement loop are the next work, not implemented yet.

Default observations are RGB plus robot proprioception. `--privileged` also
exposes object poses for initial debugging. Label those runs separately; they are
not comparable to vision-only benchmark results. This harness measures a small
LIBERO task subset under an agent protocol, not the published full LIBERO protocol.

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

See [PLAN.md](PLAN.md) for the concrete demo and [program.md](program.md) for the
agent-loop rules.
