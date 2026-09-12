# Hackathon demo viewer

A local, read-only viewer for the existing robot experiment artifacts. It uses Python's
standard library and plain HTML/CSS/JavaScript. It does not import the executor,
simulator, improvement agent, or any third-party dependency.

## Launch in WSL

The implementation lives in its own worktree and branch:

- Worktree: `/home/ludvig/autoresearch-demo`
- Branch: `ludvig/hackathon-demo`
- Owned changes: `demo/` and this document only.

```bash
cd /home/ludvig/autoresearch-demo
python3 -m demo.serve \
  --run /home/ludvig/autoresearch-architecture/runs/architecture-live-baseline \
  --port 8765
```

Open **http://127.0.0.1:8765** in the Windows browser. WSL localhost forwarding
was verified on this machine. No installation or `uv sync` is needed.
The server binds only to IPv4 localhost. Ctrl+C stops a foreground launch.

For an ongoing loop, use the loop directory containing `loop.json`, `baseline/`
and `iteration-001/`. The directory must exist, but it can initially be empty.
Restart the viewer with a different `--run` to select another experiment.
Use `--port 8767` if the existing presentation server still owns 8765.

```bash
python3 -m demo.serve --run /absolute/path/to/loop --port 8767
```

The browser checks for artifacts every three seconds while visible. The loop status
is recorded metadata, not a live process-health probe. Polling does not reset an
unchanged video or its playback position. Camera and state controls are independent
for each condition. A missing/unplayable video falls back to available stills.

## Saved-run replay

Point at an already saved run, with polling disabled:

```bash
python3 -m demo.serve \
  --run /home/ludvig/autoresearch-architecture/runs/architecture-live-baseline \
  --port 8767 --replay
```

For a stable presentation, first copy a **completed** run directory to a separate
saved-run directory, then launch against that copy. `--replay` disables browser
polling; it does not freeze files or produce a recording. The Refresh button still
works; Resume updates explicitly re-enables polling.

Copy the whole selected run directory when handing off a loop, preserving relative
paths. Recorded absolute comparison paths are remapped only to recognized local
`baseline` or `iteration-NNN/candidate` directories. The viewer never opens
external paths from JSON. Media playback uses HTTP byte ranges and supports seeking.

## Presentation controls

The main screen fits a 1920x1080 presentation viewport. Narrower/shorter windows
scroll normally; mobile has a single-column layout without horizontal overflow.

- **Experiment view:** latest available iteration, or a specific recorded iteration.
- **State selector:** select an episode independently in each condition; headline
  metrics remain clearly labelled batch totals.
- **Camera selector:** external video, external stills, or wrist stills.
- **Still slider:** inspect original frames by recorded physics-step filename.
- **Inspect run:** selected episode result, declared batch protocol, last 60 motion
  records, latest requested/achieved pose, and original artifact links.
- **Read skills:** tested guide, proposal, accepted comparison snapshot, and diff.
- **Evidence:** diagnosis, prediction, citations, uncertainty, missing evidence,
  and links to indexed sources.
- **Selection details:** the original comparison fields.
- **Pause updates:** freeze the screen's data while narrating; video remains usable.

Video times are independent simulated playback times. They do not represent API
latency, and the screen makes no claim that corresponding actions are synchronized.

## Artifact contract and assumptions

The adapter inspects the actual executor example and integration-agent schema as
present on 12 September 2026. No artifact is modified.

| Artifact | Fields consumed and interpretation |
| --- | --- |
| `experiment.json` | `model`, `smoke`, `skill_hash`; standalone configuration or `raw/state-NNN/` configuration |
| `state-NNN/result.json` | Boolean `success` is the only task-outcome source; `steps`, `api_requests_attempted`, `init_state_id`, `instruction`, `requested_model`, `termination`, `error`, `policy` |
| `state-NNN/episode.json` | Instruction fallback while the result is absent |
| `requests.jsonl` | Complete object records with integer `request`; used only when the episode lacks a recorded `api_requests_attempted` |
| `control.jsonl` | `start_step`, `end_step`, `requested_position`, `achieved_position`, `requested_rotation`, `achieved_rotation`, `position_error`, `rotation_error`, `reached`, `stop_reason`, `gripper_command`; additional fields such as `inspect_chunk_final` are preserved in the raw/latest record |
| `episode.mp4`, `<step>-agentview.png`, `<step>-robot0_eye_in_hand.png` | Original external video and numerically ordered camera stills; filenames establish still step labels only |
| `manifest.json` | `state_ids`, `skill_hash`, `policy`, declared executor protocol shown in details |
| `summary.json` | `episodes` and `successes` are checked against available result files; summary alone never establishes task success |
| `loop.json` | `status`, `fixture`; iteration directories are discovered as files appear |
| `iteration-NNN/improvement/improvement.json` | `decision` (`propose` or `defer`), `diagnosis`, `evidence` citation strings, `uncertainty`, `prediction`, `candidate_markdown` |
| `iteration-NNN/improvement/evidence.json` | `tested_skill_files`, `files_sha256` index, `source_episode`, `missing_evidence`, `fixture`; indexed paths are relative to the local `improvement/evidence/` directory |
| `comparison.json` | `decision` (`keep`, `reject`, `inconclusive`), `baseline`, `candidate`, `reasons`, `baseline_successes`, `candidate_successes`, `attempts_per_condition`; all fields available in details |
| Markdown snapshots | Batch `skills/**/*.md`, improvement `skills/**/*.md`, tested evidence `skills/**/*.md`, and accepted evidence `incumbent/**/*.md` |

The ongoing loop can initially contain
`baseline/raw/state-000/state-000/` before the coordinator copies its result
to `baseline/state-000/`. The viewer handles both and avoids double counting.

Success counts are sums of available, boolean episode outcomes. A policy error is
never displayed as a success, even if an inconsistent artifact says otherwise.
The denominator is **completed recorded outcomes**, with expected batch size shown
separately when declared. Missing/invalid counts display **not measured**. Physics
steps are summed only if every discovered/declared episode has a recorded integer
step count; no counts are inferred from waypoints or video duration. API counts
include attempted executor requests and retries where recorded; no improver costs
or dollar estimates are invented. A partial request log does not establish an exact
total, so the fallback total remains unmeasured.

`reached` refers to an intermediate motion waypoint. It never means the drawer
task succeeded. The viewer does not run or recompute the evaluator's promotion
checks; it reports the recorded decision and exposes the source.

For iteration 2 and later, the comparison condition can be an accepted earlier
candidate. It is labelled **Accepted incumbent**. Independently, the skill diff uses
the *tested* snapshot copied into the diagnosis evidence. A rejected skill can have
been tested without being accepted. If no evidence package exists, the batch skill
snapshot is the display fallback; the source is labelled.

A known empty skill hash establishes a no-skill standalone baseline. Missing
snapshots otherwise remain unknown. Proposed Markdown text falls back to the
exported proposal files when not embedded in the report.

Missing files are expected while a run is active. Malformed/truncated JSON is
reported in Artifact notices and retried on the next poll. Complete JSONL records
remain inspectable when the final line is partial. Individual text reads are capped
at 16 MiB and file indexing at 10,000 eligible artifacts. Large text artifacts remain
available through original-source links. The UI escapes artifact text by using DOM
text nodes, including Markdown and evidence citations.

The loop-stage highlight means the latest **available artifact stage**, not proof
that a worker is currently executing it. Keep, reject, inconclusive, deferred,
pending, and a standalone no-comparison state are distinct.

## File-serving boundary

Only the three viewer assets and eligible artifacts inside the selected run are
served. There is no repository route, directory listing, upload, or write endpoint.
Unknown filenames, dotfiles, traversal components, backslashes and symlink paths
are rejected. On WSL, directory-relative `O_NOFOLLOW` opens prevent symlink swaps
during file reads. The server never reads `.env`. Original JSON/Markdown links are
served as plain text with `nosniff`; artifact HTML is not served.

The server targets Python 3.10+ on Linux/WSL because its file-opening boundary uses
Linux `dir_fd`, `O_DIRECTORY`, and `O_NOFOLLOW`. The browser runs on Windows.
No dependency or robotics runtime setup is required.

## Verification

```bash
cd /home/ludvig/autoresearch-demo
python3 -m unittest demo.test_demo -v
```

Tests use temporary, visibly synthetic data and a real localhost server. Coverage:
authoritative results, partial files, missing metrics, request counts, multi-iteration
incumbent selection, tested-skill diff, deferred state, in-progress raw episodes,
invalid shapes/non-finite JSON, symlink metadata and skill escapes, forbidden paths,
source allowlisting, and video byte ranges.

Browser verification used the genuine
`architecture-live-baseline` saved run: **0/1 LIBERO success, 240 physics steps,
2 executor requests**. Its 256x256 video loads as 12.25 seconds of playback, can
seek, and advances across a polling interval without pausing/resetting. External/
wrist still selection and motion evidence details were inspected. This validates
the viewer, not a new robotics result.

A separate, explicitly labelled synthetic loop verified the diff, reject decision,
evidence links, and saved-replay mode. Script-looking Markdown remained inert text.
No synthetic loop is presented as a real improvement result.

Screenshot: [genuine saved run](../demo/verification/genuine-run.png).
No held-out state was executed; no model was invoked.

## 90-second walkthrough

**0–15 seconds — task and idea**

“Here the robot is trying to open the middle drawer. Our goal is for experience
to improve the robot's next attempt without changing its model weights. The
memory is a readable operating guide.”

**15–35 seconds — show the recorded attempt**

[Play the baseline video.]

“This is an actual recorded attempt. The task result comes from LIBERO's success
predicate. This saved run used 240 physics steps and two API requests, and it did
not complete the task. Reaching a motion target is different from completing it.”

**35–60 seconds — show memory and evidence**

[If genuine revision artifacts are available, open Read skills and Evidence.]

“Between attempts, a second agent reviews images, actions and measured movement.
It proposes a bounded change to the operating guide, with an evidence-backed
hypothesis, uncertainty and a prediction we can check on the next run.”

[If no revision exists, say: “This saved attempt has no revision yet; these panels
will show the exported revision and its evidence when the loop produces them.”]

**60–80 seconds — show the retest and decision**

[Show a genuine candidate and decision if available.]

“The evaluator compares fresh development attempts under the same model, tools
and controller. It keeps a candidate only for a strict increase in success count.
This decision is recorded, and rejected proposals stay distinct from accepted
skills.”

[If no comparison exists, say: “No learning gain has been established from this
saved attempt. We are showing the control evidence and the viewer's integration
point, not claiming that the learning loop has already improved task success.”]

**80–90 seconds — close**

“The next question is whether validated operating knowledge transfers to unfamiliar
situations. We preserve the evidence so that claim can be tested.”

## Integration

No integration changes are needed to run the viewer from its own worktree while
reading the integration worktree's artifacts. This is the fastest hackathon path.

To include it later, the integration owner can cherry-pick the delivered commit
(or this branch) when their worktree is ready:

```bash
git -C /home/ludvig/autoresearch-architecture cherry-pick ludvig/hackathon-demo
```

Only `demo/` and `docs/DEMO.md` are included. Do not reset, stash, clean, or
switch the integration branch to make room for it. The final handoff supplies the
exact commit hash for pinning the cherry-pick.
