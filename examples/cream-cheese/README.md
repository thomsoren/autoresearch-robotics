# Recorded development result

The fixed Fable 5.1 actor completed the LIBERO cream-cheese task on 1/3 starting
states with no guide, 1/3 with the first proposed guide, and 2/3 with its second
revision. The first revision was rejected; the second was retained. All nine
episodes finished without execution errors. `results.json` records the counts;
`SKILL.md` is the selected guide, copied unchanged from its run snapshot.

This is a small development result, not held-out or cross-task validation. The
author's explanations remain hypotheses; acceptance validates measured outcomes,
not every heuristic in its prose. The compact JSON is not a complete evaluator
batch and must not be passed to `evaluation.evaluate --compare`.

## Show the demo on another computer

Clone `https://github.com/thomsoren/lito`, branch
`ludvig/autoresearch-demo-page`, then run from that repository:

```sh
bun install --frozen-lockfile
bun run dev:demo
```

The standalone `/autoresearch.html` page bundles the three recorded videos and
camera images in Git. It needs no robot runtime, backend configuration or API key.
See `app/web/AUTORESEARCH-DEMO.md` there for details.

## Run the selected guide on a robot episode

After the Linux/macOS setup in `docs/SETUP.md` and an API key in ignored `.env`:

```sh
PYTHONPATH=. uv run --group evaluation --group execute -m execute.inspect_agent \
  --model claude-fable-5-1 --control pose --task-id 6 --state 2 \
  --max-calls 24 --max-steps 1800 --timeout 900 \
  --skill examples/cream-cheese/SKILL.md --output runs/cream-cheese-replay-new
```

This starts a new paid model-controlled episode; its outcome is not guaranteed.
Use `evaluation.loop` as documented in `docs/IMPLEMENTATION.md` for further
propose/test/select iterations. Generated traces, environments, caches and secrets
remain excluded from Git.
