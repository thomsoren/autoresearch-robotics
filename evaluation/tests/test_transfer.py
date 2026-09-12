"""Frozen evaluation boundaries without simulator or API calls."""

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")
pytest.importorskip("inspect_robots_agent")
from evaluation import loop, transfer  # noqa: E402


def write_batch(
    path,
    *,
    states,
    protocol,
    successes=(),
    skill_text=None,
    control="pose",
    evaluation_split=None,
):
    path.mkdir(parents=True)
    if skill_text is not None:
        skill = path / "skills/task/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(skill_text)
    fingerprint = loop.skill_hash(path / "skills" if skill_text is not None else None)
    manifest = {
        "policy": "inspect-robots-agent",
        "suite": "libero_goal",
        "task_id": 0,
        "state_ids": list(states),
        "seed": 0,
        "max_steps": 300,
        "skill_hash": fingerprint,
        "observation_mode": "rgb_proprio",
        "control": control,
        "executor_protocol": protocol,
        "simulator_sha256": "sim",
        "evaluator_sha256": "eval",
        "dependency_lock_sha256": "lock",
        "episode_seeds": {},
        "initial_frame_sha256": {},
    }
    if evaluation_split is not None:
        manifest["evaluation_split"] = evaluation_split
    rows = []
    for state in states:
        row = {
            "policy": manifest["policy"],
            "suite": manifest["suite"],
            "task_id": manifest["task_id"],
            "init_state_id": state,
            "seed": state + 100,
            "max_steps": manifest["max_steps"],
            "observation_mode": manifest["observation_mode"],
            "control": control,
            "skill_hash": fingerprint,
            "success": state in successes,
            "steps": state + 1,
            "error": None,
        }
        if evaluation_split is not None:
            row["evaluation_split"] = evaluation_split
        episode = path / f"state-{state:03d}"
        episode.mkdir()
        manifest["episode_seeds"][str(state)] = row["seed"]
        manifest["initial_frame_sha256"][str(state)] = {}
        for camera in ("agentview", "robot0_eye_in_hand"):
            # Synthetic bytes: these tests validate evidence hashes, not image decoding.
            frame = episode / f"0000-{camera}.png"
            frame.write_bytes(f"fixture-{state}-{camera}".encode())
            manifest["initial_frame_sha256"][str(state)][camera] = hashlib.sha256(
                frame.read_bytes()
            ).hexdigest()
        loop.save(episode / "result.json", row)
        rows.append(row)
    (path / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {
        **manifest,
        "episodes": len(rows),
        "successes": sum(row["success"] for row in rows),
        "success_rate": sum(row["success"] for row in rows) / len(rows),
        "policy_errors": 0,
        "mean_steps": sum(row["steps"] for row in rows) / len(rows),
    }
    loop.save(path / "manifest.json", manifest)
    loop.save(path / "summary.json", summary)
    return path


@pytest.fixture
def frozen_loop(tmp_path, monkeypatch):
    root = tmp_path / "development"
    root.mkdir()
    config = {
        "model": "claude-opus-5",
        "effort": "low",
        "max_calls": 12,
        "max_steps": 300,
        "episode_timeout": 180,
        "suite": "libero_goal",
        "task_id": 0,
        "seed": 0,
        "control": "pose",
    }
    identity = {"fixture": "unchanged"}
    base_prompt = "base prompt"
    protocol = {
        "model": "claude-opus-5",
        "base_prompt_sha256": hashlib.sha256(base_prompt.encode()).hexdigest(),
        "executor_revision": hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest(),
        "executor_config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "max_turns": 12,
        "max_api_requests": 12,
        "max_budget_usd": None,
        "timeout_seconds": 180,
        "fresh_session_per_episode": True,
    }
    frozen = {
        "protocol": protocol,
        "identity": identity,
        "base_prompt": base_prompt,
        "executor_config": config,
    }
    skill_text = "---\nname: drawer\ndescription: test skill\n---\nObserve.\n"
    baseline = write_batch(root / "baseline", states=[0, 1, 2], protocol=protocol)
    candidate = write_batch(
        root / "iteration-001/candidate",
        states=[0, 1, 2],
        protocol=protocol,
        successes=[0, 1],
        skill_text=skill_text,
    )
    loop.save(root / "protocol.json", frozen)
    loop.save(
        root / "loop.json",
        {
            "status": "iteration_limit",
            "fixture": False,
            "selected_batch": str(candidate),
            "selected_skill": str(candidate / "skills/task/SKILL.md"),
            "active_skills_updated": False,
            "iterations": [
                {"decision": "keep", "candidate": str(candidate), "baseline": str(baseline)}
            ],
        },
    )
    monkeypatch.setattr(loop, "profile", lambda args: frozen)
    return root, frozen, skill_text


def test_frozen_evaluation_runs_original_baseline_and_selected_snapshot(
    frozen_loop, tmp_path, monkeypatch
):
    development, frozen, skill_text = frozen_loop
    calls = []

    def fake_batch(args, output, skill, current, deadline, states, split):
        calls.append((output.name, skill, list(states), args.control))
        return write_batch(
            output,
            states=states,
            protocol=current["protocol"],
            successes=[3] if skill is None else [3, 4],
            skill_text=None if skill is None else Path(skill).read_text(),
            evaluation_split=split,
        )

    monkeypatch.setattr(loop, "batch", fake_batch)
    monkeypatch.setattr(
        loop, "compare_runs", lambda *_a, **_k: pytest.fail("held-out data cannot select skills")
    )
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "held-out"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
            "4",
        ]
    )

    report = transfer.run(args)

    assert calls[0] == ("baseline", None, [3, 4], "pose")
    assert calls[1][0:1] == ("selected",)
    assert Path(calls[1][1]).read_text() == skill_text
    assert report["comparison"] == {
        "baseline_successes": 1,
        "selected_successes": 2,
        "success_delta": 1,
    }
    assert [row["init_state_id"] for row in report["conditions"]["selected"]["outcomes"]] == [
        3,
        4,
    ]
    assert report["selection_performed"] is False
    assert report["improvement_invoked"] is False
    assert report["active_skills_updated"] is False
    assert report["conditions"]["selected"]["outcomes"][0]["evaluation_split"] == "held_out"
    assert json.loads((tmp_path / "held-out/frozen-evaluation.json").read_text()) == report


@pytest.mark.parametrize("mismatch", ["episode_seeds", "initial_frame_sha256", "missing_frames"])
def test_frozen_evaluation_rejects_unmatched_initial_conditions(
    frozen_loop, tmp_path, monkeypatch, mismatch
):
    development, _, _ = frozen_loop

    def fake_batch(args, output, skill, current, deadline, states, split):
        batch = write_batch(
            output,
            states=states,
            protocol=current["protocol"],
            successes=[] if skill is None else states,
            skill_text=None if skill is None else Path(skill).read_text(),
            evaluation_split=split,
        )
        if output.name == "selected":
            manifest = loop.read(batch / "manifest.json")
            if mismatch == "episode_seeds":
                manifest["episode_seeds"][str(states[0])] += 1
            elif mismatch == "missing_frames":
                del manifest["initial_frame_sha256"]
            else:
                frame = batch / f"state-{states[0]:03d}/0000-agentview.png"
                frame.write_bytes(b"different valid initial observation")
                manifest["initial_frame_sha256"][str(states[0])]["agentview"] = hashlib.sha256(
                    frame.read_bytes()
                ).hexdigest()
            loop.save(batch / "manifest.json", manifest)
            summary = loop.read(batch / "summary.json")
            if mismatch == "missing_frames":
                del summary["initial_frame_sha256"]
            loop.save(batch / "summary.json", {**summary, **manifest})
        loop.load_batch(batch)  # Each condition is independently valid.
        return batch

    monkeypatch.setattr(loop, "batch", fake_batch)
    output = tmp_path / "held-out"
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(output),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
            "4",
        ]
    )
    with pytest.raises(ValueError, match="Held-out"):
        transfer.run(args)
    report = loop.read(output / "frozen-evaluation.json")
    assert report["status"] == "error"
    assert "comparison" not in report


def test_same_task_development_states_cannot_enter_frozen_evaluation(
    frozen_loop, tmp_path, monkeypatch
):
    development, _, _ = frozen_loop
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "2",
            "3",
        ]
    )
    with pytest.raises(ValueError, match="development states"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()


def test_same_task_frozen_evaluation_is_limited_to_reserved_states(
    frozen_loop, tmp_path, monkeypatch
):
    development, _, _ = frozen_loop
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "8",
        ]
    )
    with pytest.raises(ValueError, match="reserved states 3 through 7"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()


def test_mutated_selected_snapshot_is_rejected_before_held_out_execution(
    frozen_loop, tmp_path, monkeypatch
):
    development, _, _ = frozen_loop
    (development / "iteration-001/candidate/skills/task/SKILL.md").write_text("changed")
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
        ]
    )
    with pytest.raises(ValueError, match="snapshot"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()


def test_changed_frozen_identity_is_rejected_before_held_out_execution(
    frozen_loop, tmp_path, monkeypatch
):
    development, frozen, _ = frozen_loop
    monkeypatch.setattr(loop, "profile", lambda args: {**frozen, "identity": {"fixture": "new"}})
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
        ]
    )
    with pytest.raises(ValueError, match="changed"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()


def test_development_control_config_mismatch_is_rejected_before_execution(
    frozen_loop, tmp_path, monkeypatch
):
    development, _, _ = frozen_loop
    for name in ("manifest.json", "summary.json"):
        path = development / "baseline" / name
        data = loop.read(path)
        data["control"] = "xyz"
        loop.save(path, data)
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
        ]
    )
    with pytest.raises(ValueError, match="frozen executor configuration"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()


def test_modified_stored_executor_budget_is_rejected_before_execution(
    frozen_loop, tmp_path, monkeypatch
):
    development, _, _ = frozen_loop
    path = development / "protocol.json"
    stored = loop.read(path)
    stored["executor_config"]["max_calls"] = 5
    loop.save(path, stored)
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
        ]
    )
    with pytest.raises(ValueError, match="configuration hash"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize(
    ("config_name", "replacement"),
    [("max_calls", 5), ("episode_timeout", 60), ("model", "other-model")],
)
def test_stored_config_must_match_protocol_limits(
    frozen_loop, tmp_path, monkeypatch, config_name, replacement
):
    development, _, _ = frozen_loop
    path = development / "protocol.json"
    stored = loop.read(path)
    stored["executor_config"][config_name] = replacement
    stored["protocol"]["executor_config_sha256"] = hashlib.sha256(
        json.dumps(stored["executor_config"], sort_keys=True).encode()
    ).hexdigest()
    loop.save(path, stored)
    monkeypatch.setattr(loop, "batch", lambda *_a, **_k: pytest.fail("must reject before running"))
    args = transfer.arguments(
        [
            "--loop",
            str(development),
            "--output",
            str(tmp_path / "invalid"),
            "--suite",
            "libero_goal",
            "--task-id",
            "0",
            "--states",
            "3",
        ]
    )
    with pytest.raises(ValueError, match="protocol limits"):
        transfer.run(args)
    assert not (tmp_path / "invalid").exists()
