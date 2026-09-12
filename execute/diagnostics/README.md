# Scripted control diagnostics

Bounded, no-LLM diagnostics for LIBERO Goal task 0 ("open the middle drawer of
the cabinet"), state 0. They exist so controller behaviour can be measured
without API or model variability. A scripted diagnostic is **not** an LLM result
and never establishes task success; only LIBERO's predicate does.

Run from the repository root. Each script needs `PYTHONPATH=.` because the
project sets `package = false`, and `Robot` refuses to reuse an output folder,
so delete the run directory before repeating one.

```bash
PYTHONPATH=. uv run --locked python execute/diagnostics/gain.py runs/diag-gain
PYTHONPATH=. uv run --locked python execute/diagnostics/settle.py runs/diag-settle
PYTHONPATH=. uv run --locked python execute/diagnostics/axis.py
PYTHONPATH=. uv run --locked python execute/diagnostics/reach.py
PYTHONPATH=. uv run --locked python execute/diagnostics/reach2.py
```

| Script | Question it answers |
| --- | --- |
| `gain.py` | What fraction of a commanded displacement is actually achieved? |
| `settle.py` | Is the shortfall unfinished settling, and does `move_to` converge? |
| `scene.py` | Where are the objects and the cabinet's joints and regions? |
| `geom.py` | Which geoms form the middle drawer's handle? |
| `axis.py` | Which way does the drawer slide, and where is the success threshold? |
| `contact.py` | What geometry blocks the approach, and at which position? |
| `reach.py` | Can any fixed-orientation approach offset reach the handle? |
| `reach2.py` | Is the handle's depth reachable at all, or is it a workspace limit? |
| `grasp.py` | Does adding wrist rotation let the gripper straddle the handle? |

`scene.py`, `geom.py`, `axis.py`, `contact.py`, `reach.py`, `reach2.py` and
`grasp.py` read privileged simulator state for **measurement only**. That state
is never exposed to a policy, nothing is artificially attached, and the benchmark
predicate is never replaced. The numbers they produce are pinned by
`execute/test_control_diagnosis.py`.
