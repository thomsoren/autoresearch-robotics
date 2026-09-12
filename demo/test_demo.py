"""Synthetic fixtures used only for viewer tests, never benchmark results."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from demo.artifacts import snapshot
from demo.serve import make_server


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, obj):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))

    def test_only_result_boolean_is_task_success(self):
        self.put("experiment.json", {"model": "fixture-model", "smoke": True})
        self.put("state-000/result.json", {"success": False, "steps": 240,
                 "api_requests_attempted": 2, "init_state_id": 0})
        self.put("state-000/control.jsonl", {"reached": True, "end_step": 240})
        data = snapshot(self.root)
        batch = data["baseline"]
        self.assertTrue(data["fixture"])
        self.assertEqual(batch["successes"], 0)
        self.assertEqual(batch["completed"], 1)
        self.assertEqual(batch["steps"], 240)
        self.assertEqual(batch["requests"], 2)
        self.assertEqual(data["decision"], "not_available")
        self.assertEqual(batch["episodes"][0]["outcome"], "failure")

    def test_partial_files_do_not_become_zero_or_success(self):
        self.put("experiment.json", {"model": "fixture"})
        self.put("state-000/result.json", {"success": "true"})
        self.put("state-000/episode.json", {"init_state_id": 0})
        (self.root / "state-000/result.json").write_text('{"success":')
        (self.root / "requests.jsonl").write_text('{"request": 1}\n{"request":')
        data = snapshot(self.root)
        self.assertIsNone(data["baseline"]["successes"])
        self.assertIsNone(data["baseline"]["steps"])
        self.assertIsNone(data["baseline"]["requests"])
        self.assertEqual(data["baseline"]["episodes"][0]["outcome"], "pending")
        self.assertTrue(data["warnings"])

    def test_complete_request_records_are_counted_when_result_count_absent(self):
        self.put("experiment.json", {"model": "fixture"})
        self.put("state-000/result.json", {"success": False, "steps": 8})
        (self.root / "requests.jsonl").write_text('{"request": 1}\n{"request": 2}\n')
        self.assertEqual(snapshot(self.root)["baseline"]["requests"], 2)

    def test_loop_diff_uses_tested_skill_and_comparison_uses_incumbent(self):
        self.put("loop.json", {"status": "iteration_limit", "fixture": True})
        for batch, wins in [("baseline", [False, False, False]),
                            ("iteration-001/candidate", [True, False, False]),
                            ("iteration-002/candidate", [False, False, False])]:
            self.put(batch + "/manifest.json", {"state_ids": [0, 1, 2]})
            for state, win in enumerate(wins):
                self.put(batch + f"/state-{state:03}/result.json",
                         {"success": win, "steps": 10, "api_requests_attempted": 1})
        self.put("iteration-001/comparison.json",
                 {"decision": "keep", "baseline": "/old/run/baseline",
                  "candidate": "/old/run/iteration-001/candidate"})
        self.put("iteration-002/comparison.json",
                 {"decision": "reject", "baseline": "/old/run/iteration-001/candidate",
                  "candidate": "/old/run/iteration-002/candidate",
                  "reasons": ["No strict gain"]})
        self.put("iteration-002/improvement/improvement.json",
                 {"decision": "propose", "diagnosis": "Fixture diagnosis",
                  "evidence": ["control.jsonl:1"], "uncertainty": ["Fixture"],
                  "prediction": "Verify motion", "candidate_markdown": "New guide"})
        self.put("iteration-002/improvement/evidence.json",
                 {"fixture": True, "tested_skill_files": ["skills/task/SKILL.md"],
                  "incumbent_skill_files": ["incumbent/task/SKILL.md"],
                  "files_sha256": {"skills/task/SKILL.md": "fixture"}})
        path = self.root / "iteration-002/improvement/evidence/skills/task/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("Previously tested guide")
        data = snapshot(self.root, "iteration-002")
        self.assertEqual(data["decision"], "reject")
        self.assertEqual(data["baseline"]["successes"], 1)
        self.assertEqual(data["candidate"]["successes"], 0)
        self.assertIn("Previously tested guide", data["skills"]["tested"])
        self.assertIn("-Previously tested guide", data["skills"]["diff"])
        self.assertIn("+New guide", data["skills"]["diff"])

    def test_summary_alone_does_not_prove_success(self):
        self.put("baseline/summary.json", {"episodes": 3, "successes": 3})
        self.put("loop.json", {"status": "running"})
        data = snapshot(self.root)
        self.assertIsNone(data["baseline"]["successes"])

    def test_deferred_and_running_raw_episode(self):
        self.put("loop.json", {"status": "deferred"})
        self.put("baseline/raw/state-000/state-000/episode.json", {"init_state_id": 0})
        self.put("baseline/raw/state-000/experiment.json", {"model": "fixture"})
        self.put("iteration-001/improvement/improvement.json", {"decision": "defer"})
        data = snapshot(self.root)
        self.assertEqual(data["decision"], "deferred")
        self.assertEqual(data["baseline"]["episodes"][0]["outcome"], "pending")

    def test_symlinked_metadata_is_not_read(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.json"
            secret.write_text('{"model": "SECRET"}')
            (self.root / "experiment.json").symlink_to(secret)
            data = snapshot(self.root)
            self.assertNotIn("SECRET", json.dumps(data))

    def test_wrong_shape_and_nonfinite_artifacts_do_not_break_snapshot(self):
        self.put("loop.json", {"status": "running"})
        self.put("baseline/manifest.json", {"state_ids": None})
        self.put("iteration-001/improvement/evidence.json", {"tested_skill_files": None})
        self.put("iteration-001/improvement/improvement.json", {"diagnosis": "partial"})
        self.put("baseline/state-000/result.json", {"success": False, "steps": float("nan")})
        data = snapshot(self.root)
        self.assertIsNone(data["baseline"]["steps"])
        json.dumps(data, allow_nan=False)

    def test_nested_symlink_cannot_supply_skill_text(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "SKILL.md"
            path.write_text("SECRET")
            (self.root / "skills").symlink_to(outside, target_is_directory=True)
            self.assertNotIn("SECRET", json.dumps(snapshot(self.root)))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "episode.mp4").write_bytes(b"0123456789")
        (self.root / ".env").write_text("DO NOT EXPOSE")
        self.server = make_server(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def test_video_ranges_support_seeking(self):
        status, headers, body = self.get("/artifacts/episode.mp4", {"Range": "bytes=2-5"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"2345")
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")

    def test_rejects_traversal_hidden_files_and_directories(self):
        for path in ["/artifacts/../.env", "/artifacts/%2e%2e/.env",
                     "/artifacts/%252e%252e/.env", "/artifacts/.env",
                     "/artifacts/", "/.git/config", "/artifacts/a%5c..%5c.env"]:
            with self.subTest(path=path):
                self.assertIn(self.get(path)[0], [400, 403, 404])

    def test_symlink_escape_and_unrelated_file_are_not_served(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "episode.mp4"
            target.write_bytes(b"PRIVATE")
            (self.root / "escape.mp4").symlink_to(target)
            self.assertEqual(self.get("/artifacts/escape.mp4")[0], 404)
        (self.root / "private.json").write_text('{"secret": true}')
        self.assertEqual(self.get("/artifacts/private.json")[0], 404)

    def test_invalid_and_suffix_ranges(self):
        self.assertEqual(self.get("/artifacts/episode.mp4", {"Range": "bytes=50-"})[0], 416)
        self.assertEqual(self.get("/artifacts/episode.mp4", {"Range": "bytes=-3"})[2], b"789")

    def test_snapshot_endpoint_and_asset(self):
        self.assertEqual(self.get("/")[0], 200)
        status, _, body = self.get("/api/snapshot")
        self.assertEqual(status, 200)
        self.assertIn("baseline", json.loads(body))
        self.assertEqual(self.get("/api/snapshot?iteration=../../")[0], 400)


if __name__ == "__main__":
    unittest.main()
