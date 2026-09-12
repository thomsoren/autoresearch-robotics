# Robot skills

Earlier notes for Laksiya's future skill workspace. No learned skill is supplied yet.

Start with one Markdown procedure containing: when to use it, observations to
check, motion/gripper steps, evidence of progress, and a bounded recovery rule.
Laksiya's adapter should explicitly load the evaluator's snapshot path. Ignore
this planning document when sending skill content to the acting model.

Keep versioned evidence in `runs/`; the evaluator copies and hashes skills for
each evaluation. Only the outer improvement agent edits the live skill directory,
between runs. The acting agent should read the frozen snapshot.
