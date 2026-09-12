# Hackathon viewer implementation plan

Goal: present recorded robot evidence and skill selection in a local, read-only viewer.
Architecture: Python standard-library artifact adapter and localhost HTTP server; plain browser assets.
Scope: demo/ and docs/DEMO.md only. No simulator, model requests, dependencies, or held-out runs.
Spec: user-supplied hackathon viewer brief (12 September 2026).

- [x] Artifact adapter: test real temporary files for authoritative boolean results, incomplete JSON/JSONL, loop iteration selection, and tested versus incumbent skills. Implement demo/artifacts.py.
- [x] HTTP boundary: test real localhost requests for traversal, symlink and hidden-file rejection, and byte ranges. Implement demo/serve.py with explicit assets and read-only artifact routes.
- [x] Presentation: build demo/index.html, demo/style.css, demo/app.js. Show independently controlled media, exact metrics, readable diff, cited diagnosis, selection decision and iteration/state controls. Poll without resetting playback. Replay disables polling.
- [x] Verification: unittest, genuine saved-run API check and browser interaction/playback checks. Document schema and a 90-second script in docs/DEMO.md. Commit only owned files and give cherry-pick instructions.

Execution is inline in the authorized separate worktree.
