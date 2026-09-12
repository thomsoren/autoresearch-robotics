> The user authorized end-to-end implementation across the earlier ownership
> boundaries on the `ludvig/skill-architecture` branch. For its current state,
> see [IMPLEMENTATION.md](IMPLEMENTATION.md). The original handoff follows.

# Team handoff

The MVP is **a fixed robot executor whose Markdown skills improve LIBERO success
through an agent loop**. Thomas has built an executor in `execute/`, the simulator,
and the evaluation/improvement coordinator. Extend this implementation rather
than starting a second harness. Current task: LIBERO Goal task 0, opening the
middle drawer. Keep the structure lean and use `uv`.

## Current state

- MuJoCo/LIBERO renders and runs on native Apple Silicon; the simulation Docker
  path was smoke-tested on ARM64 Linux. The complete autonomous loop has not been
  run in Docker. See [SETUP.md](SETUP.md) for platform details.
- `execute/inspect_agent.py`: stock Inspect Robots agent, external/wrist images,
  measured end-effector/finger state, **XYZ + gripper with fixed orientation**.
  Its tools are `move_by`, `done`, `give_up`; observations arrive automatically.
  The separate `Robot` API still supports `observe`, `step`, `move_to`, `gripper`.
- `evaluation/improve.py`: one Claude Agent SDK agent diagnoses a development
  episode and proposes one Markdown skill. It receives indexed traces and images.
- `evaluation/loop.py`: fresh baseline/candidate batches on states 0,1,2, bounded
  iterations/time/requests, deterministic selection via `evaluate.py`.
- Opus 5 at standard speed executes robot commands. Prior state-0 attempts failed.
  The latest tested skill produced close/pull commands, but no LIBERO success.
- Fable 5.1 is accessible with Thomas's key, but both initial attempts were refused
  with `reasoning_extraction` before any physics steps. Exact trigger is unknown.
  Fable keeps full image history for thinking-block compatibility.
- 80 automated tests pass. A three-state coordinator smoke completed with zero API
  calls. A full paid autonomous loop and held-out improvement remain unverified.
- No candidate has been promoted. Generated skills, videos and diagnostics are
  under Thomas's ignored `runs/`; they are **not included in a fresh clone**.

## Work split

| Owner | Files | Concrete deliverable |
| --- | --- | --- |
| Ludvig | `execute/` | Diagnose approach/controller problems and demonstrate reliable handle approach, a close command and a pull attempt; aim for one genuine LIBERO success on development state 0 |
| Laksiya | `evaluation/improve.py`, `evaluation/program.md`, `evaluation/tests/test_improve.py` | Improve grounding and brevity of the skill author; produce an executable candidate from a real failure with checkable predictions |
| Thomas | `simulation/`, `evaluation/evaluate.py`, `evaluation/loop.py`, integration/docs | Maintain simulation contract, integrate both branches, run comparable batches and prepare the demo |

Keep changes on separate branches. Coordinate any robot-API or cross-owner changes
with Thomas. Preserve work in `harness/` if present in another checkout; it is not
a dependency of this MVP. Changing the controller or base prompt requires a fresh
baseline before attributing any gains to skills.

## Ludvig: copy-paste task prompt

You are joining https://github.com/thomsoren/autoresearch-robotics. Thomas has
already implemented the executor; your task is to make its robot control useful,
not build another agent framework. Work on branch `ludvig/robot-control`, primarily
in `execute/`. Read `execute/README.md`, `simulation/sim.py`, and `docs/SETUP.md`.

First run the setup and simulator smoke below. Then investigate why LIBERO Goal
state 0 repeatedly stalls before reaching the middle drawer. The current Inspect
adapter fixes wrist orientation and maps world displacement to normalized OSC
commands. In previous runs, achieved movement was often about one quarter of the
requested displacement; some commands produced almost no movement. Check whether
this comes from command semantics, controller settling, obstruction, or a mix.
Do not treat the previous LLM's collision explanation as a measured fact.

Use a bounded scripted control diagnostic first so API/model variability does not
hide controller problems. Inspect measured pose changes and both camera views.
Verify gripper semantics: positive closes, negative opens, omitted retains the
previous command; it starts open. Identify a repeatable route to the middle handle
and attempt a grasp/pull. Prefer a minimal fix in the existing adapter. If wrist
orientation is blocking the task, report evidence and propose the smallest viable
change. Coordinate changes to `simulation/` or the public tool contract with Thomas.

Deliver a command Thomas can reproduce, video/actions/result artifacts, appropriate
regression tests, and a short explanation of what is still unverified. Only LIBERO's
success predicate can establish success. A scripted diagnostic is not an LLM result;
never attach objects artificially or replace the benchmark predicate. Use only
development states 0,1,2; leave states 3–7 untouched. Do not change the improvement
agent or selection loop. After a controller change, Thomas will collect a new baseline.

## Laksiya: copy-paste task prompt

Thomas has implemented the executor, so your task is now **improvement-agent
quality**. Work on branch `laksiya/skill-grounding`, owning `evaluation/improve.py`,
`evaluation/program.md`, and `evaluation/tests/test_improve.py`. Read
`docs/DESIGN.md` and the automatic-loop section in `docs/SETUP.md`. Keep one SDK
agent for diagnosis and candidate revision. Reuse `CLAUDE_API_KEY` through the
existing isolated SDK setup. Do not create another executor or judge framework.

Audit a real failed episode supplied by Thomas or generated locally. Known failure
modes in generated advice: invented handle coordinates, equating commanded and
achieved motion, incorrect call counts, treating absent skill-load events as proof
of non-loading, and saying an open command makes all grasp/contact impossible.
The correct distinction is command vs measured movement vs visual hypothesis.
The executor also ignored parts of supplied skills, so text presence is not adherence.

Make the agent emit short task skills using only the actual executor tools, with
bounded procedures and predictions measurable from the next trace. Preserve the
existing structured report schema and evidence boundaries. Add deterministic checks
where the trace supports them; do not replace LIBERO scoring with LLM opinions.
Add tests using synthetic exports clearly labelled as fixtures, and demonstrate a
real diagnosis/proposal with evidence citations. Keep generated candidates under
`runs/`; Thomas's coordinator decides whether a complete 0,1,2 batch earns selection.
Do not touch `execute/`, `simulation/`, `evaluation/loop.py` or `evaluate.py` without
coordinating with Thomas. Never use held-out states 3–7 to revise skills.

## Setup and first checks

```bash
git clone https://github.com/thomsoren/autoresearch-robotics.git
cd autoresearch-robotics
uv run --no-project simulation/prepare.py
uv sync --locked --group evaluation --group execute
uv run --locked --group evaluation --group execute pytest -q
uv run --locked --group evaluation --group execute -m execute.inspect_agent --smoke
```

For live API runs, obtain a key from Thomas through your team's private channel
and put `CLAUDE_API_KEY=...` in your local ignored `.env`. Do not commit or paste it
into task prompts. The API key, `.vendor/`, `.venv/`, `.libero/` and `runs/` are local.

```bash
# Paid, bounded single executor attempt; creates a fresh run with video and trace.
uv run --locked --group evaluation --group execute -m execute.inspect_agent \
  --model claude-opus-5 --speed standard

# No-API integration smoke across development states 0,1,2.
uv run --locked --group evaluation --group execute -m evaluation.loop \
  --smoke --output runs/team-loop-smoke
```

Do not enable fast mode with this key for now: its organization returned a zero
fast-mode quota. Native Opus standard-speed runs work. See the setup guide for
paid-loop budgets before launching a whole loop. Share run artifacts separately
when handing off evidence; a Git push does not include ignored run folders.
