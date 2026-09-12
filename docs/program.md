# Robot skill improvement loop

This is the specification for the outer agent Laksiya will wire up. It is not
an executable loop yet.

Objective: improve development task success using reusable skill text/code.

Freeze before starting: model, task, observation mode, tool implementation,
initial-state split, seed, step budget, API budget, and time limit. The acting
agent starts a new session per episode. Keep the base prompt constant.

1. Evaluate the acting agent with no skills on development states 0, 1, 2.
2. Inspect `results.jsonl`, action traces, camera frames and agent transcripts.
3. State one failure hypothesis. Create a candidate skill under `skills/` that
   addresses it: trigger, procedure, expected evidence, and bounded recovery.
4. Evaluate a snapshot of that candidate on the same development states.
5. Keep only if success count strictly improves. On a tie, keep the incumbent
   for this MVP. Record hypothesis, old/new hashes, counts, costs and decision.
6. Repeat at most three candidate revisions, within the overall budget.
7. Freeze the incumbent and evaluate both baseline and incumbent on held-out
   states 3–7 once. No feedback from held-out runs may enter the skills.

Only change skill content between experiments. Do not change `simulation/sim.py`,
`simulation/evaluate.py`, task files, scoring, initial states, budgets or stored results.
Do not hide failed episodes or infrastructure errors. A timeout, policy error,
or exhausted budget is never a success. LIBERO decides success, not the agent.

Reusable skills should describe observation-relative actions and recovery,
not memorize exact initial-state coordinates. Record privileged-state access
if used. Do not call development success “benchmark generalization”.
