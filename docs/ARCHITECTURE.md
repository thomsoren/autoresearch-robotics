# Reusable operating skills

Approved scope: the user authorized ownership of the full architecture on 2026-09-12,
superseding the previous Thomas/Laksiya file boundaries. Gripper-tip testing is excluded.

The stock Inspect acting policy receives a frozen Markdown skill, camera images and
measured robot state. A bounded feedback adapter executes physical pose requests in
LIBERO. The separate improvement agent reads development evidence and proposes a
reusable operating guide, with an evidence-backed hypothesis and behavior prediction.
The coordinator evaluates fresh candidate episodes and selects only comparable,
strict success-count improvements. Only LIBERO's predicate establishes success.

Keep the existing execute/, simulation/ and evaluation/ implementation. Do not create
a second acting framework. Freeze model, base prompt, code, observations and budgets
during skill comparisons. New control capabilities require fresh baselines.

## Deliverables

1. Closed-loop translation and usable orientation control through the existing
   Inspect policy. Every underlying physics step consumes the episode budget.
   Zero movement can advance gripper actuation. Motion reports include requested
   and achieved pose, residual error, step interval and stop status.
2. An immutable evidence package with control records, original images, failures,
   successful regression examples, tested skill and selected incumbent distinguished.
3. Short reusable operating skills: perception, action selection, verification,
   bounded recovery. No memorized world-coordinate routes or guessed contact facts.
   Candidate validation cannot prove generality; reserved-task evaluation is needed.
4. Existing bounded train loop plus an explicit frozen evaluation mode for states
   3-7 or separately specified transfer tasks. Evaluation produces reports and never
   feeds held-out artifacts to the improver or selects skills from those outcomes.
5. Tests at control, evidence and orchestration boundaries; real simulation checks;
   a bounded live integration attempt if API access works; reproducible commands.

## Experiment limits

Development states are 0,1,2. Do not consume states 3-7 during implementation.
No artificial attachment, joint-state edits in agent episodes, or replacement scores.
Use WSL Ubuntu-24.04, existing pinned dependencies, standard-speed API requests.
Infrastructure failures defer learning. Unknown or mismatched provenance invalidates
comparison. Unsuccessful real runs must be reported honestly; architecture completion
does not imply successful learning or cross-task generalization.

## Implementation approach

Prefer the fixed executor plus learned Markdown over generated Python programs:
it preserves the current ablation and limits scope. Use one skill file initially;
no retrieval service, separate judge agent, dashboard or hardware-design subsystem.
