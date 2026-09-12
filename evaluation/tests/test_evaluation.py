"""Test evaluation integrity without creating a simulator."""

import json
from pathlib import Path

import pytest

from evaluation import evaluate


class FakeRobot:
    def __init__(self, output, init_state_id, **kwargs):
        self.output = Path(output)
        self.output.mkdir()
        self.state_id = init_state_id

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def result(self):
        return {"success": False, "steps": 1, "init_state_id": self.state_id}


def test_policy_claim_cannot_set_success_and_errors_are_counted(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluate, "Robot", FakeRobot)

    def policy(robot, skills):
        if robot.state_id == 1:
            raise RuntimeError("agent unavailable")
        return {"success": True}

    summary = evaluate.evaluate(policy, tmp_path / "eval", state_ids=[0, 1])
    assert summary["episodes"] == 2
    assert summary["success_rate"] == 0
    assert summary["policy_errors"] == 1
    rows = [json.loads(line) for line in (tmp_path / "eval/results.jsonl").read_text().splitlines()]
    assert rows[1]["termination"] == "policy_error"


def test_skill_changes_invalidate_eval(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluate, "Robot", FakeRobot)
    source = tmp_path / "source"
    source.mkdir()
    (source / "skill.md").write_text("baseline")

    def policy(robot, skills):
        (skills / "skill.md").write_text("modified")

    with pytest.raises(RuntimeError, match="modified the skill"):
        evaluate.evaluate(policy, tmp_path / "eval", skill_dir=source, state_ids=[0])
    assert (source / "skill.md").read_text() == "baseline"


def test_python_bytecode_does_not_invalidate_skill_content(tmp_path):
    (tmp_path / "skill.py").write_text("def grasp(): pass")
    before = evaluate.skill_hash(tmp_path)
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "skill.cpython-311.pyc").write_bytes(b"bytecode")
    assert evaluate.skill_hash(tmp_path) == before


def test_inspect_report_keeps_errors_in_denominator_and_preserves_evidence(monkeypatch, tmp_path):
    inspect = pytest.importorskip("inspect_robots")
    from PIL import Image

    class SuccessfulRobot(FakeRobot):
        def result(self):
            return {**super().result(), "success": True}

    monkeypatch.setattr(evaluate, "Robot", SuccessfulRobot)

    def policy(robot, skills):
        if robot.state_id == 1:
            raise RuntimeError("API unavailable")

    run = tmp_path / "eval"
    summary = evaluate.evaluate(policy, run, state_ids=[0, 1], label="test-agent")
    Image.new("RGB", (8, 8), "red").save(run / "state-000/0001-agentview.png")
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}

    report = evaluate.export_report(run)
    log = inspect.read_eval_log(str(report.with_suffix(".json")))
    assert summary["success_rate"] == log.results.metrics["libero_success"] == 0.5
    assert log.results.total_trials == 2
    assert log.results.errored_trials == 1
    assert log.samples[1].epochs[0]["libero_success"] == 0
    assert log.samples[1].status == "error"
    assert log.samples[1].error == "RuntimeError: API unavailable"
    assert log.samples[0].policy_transcripts == (None,)
    assert "data:image/png;base64," in report.read_text()
    assert "Executor transcript not imported" in report.with_suffix(".json").read_text()
    assert all((run / p).read_bytes() == data for p, data in before.items())
    with pytest.raises(FileExistsError):
        evaluate.export_report(run)


@pytest.mark.parametrize("corruption", ["summary", "missing_episode", "episode_result"])
def test_inspect_report_rejects_incomplete_or_inconsistent_batch(monkeypatch, tmp_path, corruption):
    pytest.importorskip("inspect_robots")
    monkeypatch.setattr(evaluate, "Robot", FakeRobot)
    run = tmp_path / "eval"
    evaluate.evaluate(lambda *_: None, run, state_ids=[0, 1])
    if corruption == "summary":
        path = run / "summary.json"
        summary = json.loads(path.read_text())
        summary["success_rate"] = 1
        path.write_text(json.dumps(summary))
    elif corruption == "missing_episode":
        path = run / "results.jsonl"
        path.write_text(path.read_text().splitlines()[0] + "\n")
    else:
        path = run / "state-000/result.json"
        row = json.loads(path.read_text())
        row["success"] = True
        path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        evaluate.export_report(run)
    assert not (run / "inspect").exists()


def test_report_existing_does_not_load_or_run_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        evaluate.sys,
        "argv",
        ["evaluate", "--report-existing", str(tmp_path), "--policy", "missing.executor:function"],
    )
    exported = []
    monkeypatch.setattr(evaluate, "export_report", lambda path: exported.append(path))
    monkeypatch.setattr(
        evaluate, "evaluate", lambda *a, **kw: pytest.fail("must not run an episode")
    )
    evaluate.main()
    assert exported == [tmp_path]


@pytest.fixture
def comparison_pair(monkeypatch, tmp_path):
    """Synthetic benchmark outcomes for selection tests, not robot performance."""
    protocol = {
        "model": "test-model",
        "base_prompt_sha256": "a" * 64,
        "executor_revision": "test-revision",
        "executor_config_sha256": "c" * 64,
        "max_turns": 40,
        "max_budget_usd": 1.0,
        "timeout_seconds": 300,
        "fresh_session_per_episode": True,
    }

    class ComparisonRobot(FakeRobot):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.kwargs = kwargs
            self.success = False

        def result(self):
            return {
                **super().result(),
                "success": self.success,
                **{k: self.kwargs[k] for k in ("suite", "task_id", "seed", "max_steps")},
                "observation_mode": "privileged" if self.kwargs["privileged"] else "rgb_proprio",
                "libero_revision": "test-libero",
                "settle_steps": 10,
                "task_name": "test_drawer",
                "termination": "success" if self.success else "step_limit",
            }

    monkeypatch.setattr(evaluate, "Robot", ComparisonRobot)

    def make(wins=(0, 1), error_state=None, candidate_options=None, baseline_options=None):
        source = tmp_path / "proposal"
        source.mkdir()
        (source / "SKILL.md").write_text(
            "---\nname: test_drawer\ndescription: test only\n---\nObserve."
        )

        def policy(robot, skills):
            robot.success = robot.state_id in (wins if skills else (0,))
            if skills and robot.state_id == error_state:
                raise RuntimeError("API unavailable")

        defaults = {"label": "test.executor", "state_ids": [0, 1, 2], "protocol": protocol}
        baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
        evaluate.evaluate(policy, baseline, **{**defaults, **(baseline_options or {})})
        evaluate.evaluate(
            policy, candidate, skill_dir=source, **{**defaults, **(candidate_options or {})}
        )
        return baseline, candidate

    return make, protocol


@pytest.mark.parametrize("wins,decision", [((0, 1), "keep"), ((0,), "reject"), ((), "reject")])
def test_comparison_selects_only_strict_improvement(comparison_pair, wins, decision):
    make, _ = comparison_pair
    baseline, candidate = make(wins=wins)
    before = {
        p: p.read_bytes() for run in (baseline, candidate) for p in run.rglob("*") if p.is_file()
    }
    result = evaluate.compare_runs(baseline, candidate)
    assert result["decision"] == decision
    assert result["baseline_successes"] == 1
    assert result["candidate_successes"] == len(wins)
    assert result["attempts_per_condition"] == 3
    assert result["active_skills_updated"] is False
    assert result["selected_skill_dir"] == (
        str(candidate / "skills") if decision == "keep" else None
    )
    assert all(p.read_bytes() == data for p, data in before.items())
    assert json.loads((candidate / "comparison.json").read_text())["decision"] == decision
    with pytest.raises(FileExistsError):
        evaluate.compare_runs(baseline, candidate)


def test_comparison_counts_api_error_as_failed_attempt(comparison_pair):
    make, _ = comparison_pair
    baseline, candidate = make(wins=(0, 1, 2), error_state=2)
    result = evaluate.compare_runs(baseline, candidate)
    assert result["decision"] == "keep"
    assert result["candidate_successes"] == 2
    assert result["candidate_policy_errors"] == 1
    assert result["attempts_per_condition"] == 3


@pytest.mark.parametrize(
    "change",
    [
        "model",
        "max_turns",
        "base_prompt_sha256",
        "executor_config_sha256",
        "executor_revision",
        "max_budget_usd",
        "timeout_seconds",
        "seed",
        "max_steps",
        "privileged",
        "missing_protocol",
        "noop",
    ],
)
def test_comparison_refuses_unmatched_or_unknown_protocol(comparison_pair, change):
    make, protocol = comparison_pair
    if change in protocol:
        replacement = {
            "model": "other-model",
            "max_turns": 10,
            "base_prompt_sha256": "b" * 64,
            "executor_config_sha256": "d" * 64,
            "executor_revision": "other-revision",
            "max_budget_usd": 2.0,
            "timeout_seconds": 60,
        }[change]
        options = {"protocol": {**protocol, change: replacement}}
    elif change == "missing_protocol":
        options = {"protocol": None}
    elif change == "noop":
        options = {"label": "noop"}
    else:
        options = {change: {"seed": 1, "max_steps": 10, "privileged": True}[change]}
    baseline, candidate = make(candidate_options=options)
    result = evaluate.compare_runs(baseline, candidate)
    assert result["decision"] == "inconclusive"
    assert result["reasons"]
    assert result["selected_skill_dir"] is None


@pytest.mark.parametrize("states", [[0], [3, 4, 5], [2, 1, 0]])
def test_comparison_never_selects_using_holdout_or_partial_dev(comparison_pair, states):
    make, _ = comparison_pair
    baseline, candidate = make(
        baseline_options={"state_ids": states}, candidate_options={"state_ids": states}
    )
    with pytest.raises(ValueError, match="development states"):
        evaluate.compare_runs(baseline, candidate)
    assert not (candidate / "comparison.json").exists()


def test_comparison_rejects_changed_snapshot_and_missing_batch(comparison_pair):
    make, _ = comparison_pair
    baseline, candidate = make()
    path = candidate / "skills/SKILL.md"
    path.write_text("Changed after evaluation")
    with pytest.raises(ValueError, match="snapshot"):
        evaluate.compare_runs(baseline, candidate)
    (candidate / "summary.json").unlink()
    with pytest.raises(FileNotFoundError):
        evaluate.compare_runs(baseline, candidate)
    assert not (candidate / "comparison.json").exists()


def test_comparison_checks_simulator_identity_and_unchanged_skill(comparison_pair):
    make, _ = comparison_pair
    baseline, candidate = make()
    for name in ("manifest.json", "summary.json"):
        path = candidate / name
        data = json.loads(path.read_text())
        data["simulator_sha256"] = "different"
        path.write_text(json.dumps(data))
    result = evaluate.compare_runs(baseline, candidate)
    assert result["decision"] == "inconclusive"
    assert "Different simulator_sha256" in result["reasons"]
    # A repeated comparison of exactly the same skill cannot claim an improvement.
    result = evaluate.compare_runs(candidate, candidate, output=candidate / "same-skill.json")
    assert result["decision"] == "reject"
    assert result["reasons"] == ["Skill content did not change"]


def test_invalid_protocol_fails_before_creating_run(tmp_path):
    with pytest.raises(ValueError, match="Protocol requires exactly"):
        evaluate.evaluate(lambda *_: None, tmp_path / "run", protocol={"api_key": "not-allowed"})
    assert not (tmp_path / "run").exists()
