# Inspect executor with measured pose control

The stock `inspect-robots-agent` policy operates LIBERO through our adapter.
The separate improvement agent and coordinator live in `evaluation/`.
See [architecture](../docs/ARCHITECTURE.md) and [current implementation](../docs/IMPLEMENTATION.md).

## Run in Linux, macOS, or the prepared WSL environment

```bash
cd ~/autoresearch-architecture
PYTHONPATH=. .venv/bin/python -m execute.inspect_agent --smoke --max-steps 80
PYTHONPATH=. .venv/bin/python -m execute.inspect_agent --speed standard
```

A fresh checkout needs the existing `simulation/prepare.py` and locked uv setup
in [SETUP.md](../docs/SETUP.md). Native Windows is unsupported. A `.env` entry
`CLAUDE_API_KEY` or environment variable supplies the acting/improvement key.
Requests bill the API workspace. Do not put keys in prompts or artifacts.

Defaults: `claude-fable-5-1`, standard speed, low effort, 12 HTTP attempts including
retries, 1024 maximum output tokens per response, 900 physics steps and a 300-second
wall-time limit. These are request/time/step limits, not an executor dollar cap.
Each run requires a new output directory; keep earlier evidence instead of deleting it.

## Control modes

`--control pose` (default) exposes stock `move_to`, `done`, and `give_up`.
`move_to` receives absolute targets under `targets`:

- `x,y,z`: world position in metres at the gripper control site.
- `xx,xy,xz`: tool local X axis expressed as a world unit vector.
- `yx,yy,yz`: tool local Y axis expressed as a world unit vector.
- `grip`: 0 opens, 1 closes. Omission retains the last command.

The axes are the first two columns of a rotation matrix (rot6d). Their cross
product is local Z, toward the fingertips. Supply both axes when rotating;
use intermediate orientations for large rotations. Components are unitless,
not Euler angles. Invalid axes and large interpolation jumps are rejected as
correctable tool errors before motion. No new policy framework is involved.

`--control xyz` retains stock `move_by` in world metres and a held wrist
orientation. Its `grip` convention is positive closes, negative opens,
zero/omitted retains. This legacy mode also uses measured feedback now.

Both modes execute bounded OSC goal offsets and stop on arrival, stalled
progress, per-waypoint limit or episode termination. No controller can guarantee
arrival through contact. Every underlying physics step consumes the episode
budget. Opening/closing advances physics while holding the target pose.

Observations contain external and wrist RGB, measured pose/finger positions,
physics/remaining steps and last waypoint residuals. Position and quaternion now
refer to the same gripper site, correcting the upstream body/site mismatch.
No privileged object pose, contact oracle or task geometry enters the acting policy.

Inspect's native step counts are WAYPOINTS. One waypoint consumes up to 30 physics
steps; its native predicted duration is not measured simulation or wall time.
`motion_reached` describes the last waypoint, never task completion. Only LIBERO's
predicate determines task success; calling `done` advances no physics and cannot
create success.

## Skills and artifacts

`--skill path/to/SKILL.md` freezes one Markdown file into the run and supplies
it as `prior_learnings`. It remains fixed for that episode. The executor does
not select or overwrite skills.

Each run stores `experiment.json`, `executor-trace.json`, `requests.jsonl`, native
Inspect reports under `inspect/`, and `state-NNN/` containing:

- `result.json`: authoritative outcome, termination, physics steps and API attempts.
- `actions.jsonl`: each normalized command plus measured pose and finger state.
- `control.jsonl`: requested/achieved waypoint poses, residuals, start/end physics
  steps, stop reason, and `inspect_chunk_final` marking a tool motion's last waypoint.
- `episode.mp4` and numbered external/wrist PNGs.

Video samples every fifth physics step at 4 fps; it omits LLM thinking time.
Use stills/control records to inspect the latest state. Intermediate waypoint
residuals must not be mistaken for arrival at a whole tool call's final target.
Errors remain unsuccessful attempts in our evaluator, even if native Inspect
excludes them from its own aggregate.

## Verification

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Historical pre-fix findings are retained in [DIAGNOSIS.md](DIAGNOSIS.md).
Their open-loop mapping and fixed-orientation statements describe commit3b14746,
not this branch. Scripted diagnostics are never reported as LLM results.
