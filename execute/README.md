# Inspect Robots agent experiment

This is a separate executor experiment. Laksiya's `harness/`, the simulator and
our improvement agent are unchanged. It uses the stock `inspect-robots-agent`
HTTP agent and native Inspect rollout runner, not the Claude Agent SDK.

```bash
uv sync --locked --group execute
uv run --group execute -m execute.inspect_agent --smoke
uv run --group execute -m execute.inspect_agent
```

The default is `claude-opus-5` with native Messages API fast mode, low effort,
12 HTTP requests (including retries), 1,024 maximum output tokens per response,
300 physics steps and a 180-second wall-time limit. These are bounded requests,
not a dollar spending ceiling. `CLAUDE_API_KEY` is read from root `.env` or the
environment and passed to this policy only. The direct endpoint is
`https://api.anthropic.com/v1`; use bare model IDs with this explicit endpoint.

The model runs LIBERO Goal task 0, “open the middle drawer of the cabinet,” on
state 0, seed 0. Change `--task-id`, `--state`, `--seed`, `--max-calls`,
`--max-steps`, `--timeout` or `--effort` explicitly. `--output` must be a new folder.
`--skill path/to/SKILL.md` snapshots one Markdown file as the plugin's
`prior_learnings`; it is loaded once, not discovered progressively from a folder.

If the account lacks fast-mode quota, standard speed is an explicit alternative:

```bash
uv run --group execute -m execute.inspect_agent --speed standard
```

There is no automatic fallback. The key can access Opus 5, but the first correctly
configured fast trial received HTTP 429 with an organization quota of **zero fast
input tokens/minute**. Three requests were rejected and zero physics steps ran.
That is an API access limitation, not evidence of failed robot behavior.

[Anthropic's fast-mode documentation](https://platform.claude.com/docs/en/build-with-claude/fast-mode)
requires research-preview access through an account manager or waitlist, plus
`speed: "fast"` and the `fast-mode-2026-02-01` beta header (sent by the plugin).
Fast mode has separate rate limits from standard Opus; retries cannot resolve a
zero allocation. Ask the account owner to obtain fast-mode access and quota.
[Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview)
provides a different, hosted harness with tools and skills. Its default API-account
access does not establish fast-mode access; the docs provide no quota bypass.
Using it here would require a separate integration with the local simulator.

## Control contract

The stock plugin rejects LIBERO's axis-angle rotation interface. This initial
adapter deliberately exposes **XYZ translation plus gripper, with fixed wrist
orientation**. Do not interpret its results as a test of full pose control or
compare them directly with Laksiya's four-tool executor.

`move_by` takes total world-frame displacement in meters (`dx`, `dy`, `dz`).
Inspect splits it into chunks capped at 1 cm per axis per step; the adapter maps
each to robosuite's normalized OSC commands by dividing by 0.05. All rotation
commands remain zero. `grip > 0` commands close, `< 0` commands open, and zero
or omission retains the last command. A split nonzero grip command retains its
sign. Actual motion may lag the command or be blocked by contact.

The policy receives upright external/wrist RGB images and measured end-effector
position, quaternion (xyzw), and finger qpos. No privileged object poses are sent.
Native policy tools are `move_by`, `done` and `give_up`, with images included in
each policy observation. Success is LIBERO's predicate; `done` cannot declare it.
All physics and OpenGL calls stay on the main thread. This runner targets macOS
and Linux; its wall-time alarm uses POSIX signals.

Task source:
`.vendor/libero/libero/libero/bddl_files/libero_goal/open_the_middle_drawer_of_the_cabinet.bddl`.
The existing LIBERO preparation supplies the MuJoCo model and meshes.

## Evidence and checks

Each run writes `experiment.json`, native Inspect logs/frames/actions under
`inspect/`, and our simulator's `state-000/` video, PNGs, actions and result.
`executor-trace.json` retains the plugin conversation with image omission
markers; original image evidence remains on disk. `requests.jsonl` records
HTTP status, requested/returned model, requested speed and returned usage,
without headers or API keys. Where supplied, `usage.speed` establishes the
served mode. A requested fast flag alone does not prove the server served fast.
Open `inspect/html/index.html` for the generated native reports. Setup/time-limit
errors can leave partial evidence; inspect `error.json` and the episode result.

Native Inspect excludes errored trials from its scored aggregate; the separate
LIBERO episode result marks policy/API errors unsuccessful. Do not substitute a
native aggregate for our baseline/candidate comparison denominator. No skill
selection or promotion happens in this experimental executor.

```bash
uv run --group execute pytest -q execute/test_inspect_agent.py
uv run --group evaluation --group execute pytest -q
```

Verified: the adapter smoke physically moved the arm about 4.2 mm over ten steps
and saved video. Tests cover physical-to-normalized translation, gripper-command
persistence, bounds before physics, API request limits and false `done` claims.
Actual task performance with a model is pending a permitted API execution mode.

## Fable 5.1 trial

Use the same key and optional frozen task skill:

```bash
uv run --locked --group evaluation --group execute -m execute.inspect_agent \
  --model claude-fable-5-1 --speed standard \
  --skill runs/inspect-agent-obstruction-improvement/skills/open_middle_drawer_of_cabinet/SKILL.md
```

For Fable 5.1, the adapter retains all prior images (`image_horizon=null`) because
its thinking blocks bind to the preceding conversation; removing older images
can invalidate them. The plugin already uses adaptive thinking and automatic tool
choice, which match the [Fable migration requirements](https://platform.claude.com/docs/en/models/fable-5-1/migration-guide).
Opus keeps its existing two-observation image horizon. This is a model trial with
an additional history-setting difference, not a controlled skill comparison.
The automatic loop also accepts `--model claude-fable-5-1`; the improvement model
is configured independently with `--improve-model`.

Live attempts on this account reached `claude-fable-5-1` (HTTP 200), but the
first model response was a refusal with category `reasoning_extraction`, both
with the obstruction skill (`runs/inspect-agent-fable51-obstruction`) and without
skills (`runs/inspect-agent-fable51-baseline`). Both stopped at zero physics
steps. Model access and request formatting do not establish robot performance;
the exact prompt/tool text triggering the refusal has not been identified.
