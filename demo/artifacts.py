"""Read-only projection of recorded artifacts. Never imports the robotics runtime."""
import difflib
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

JSON_NAMES = {
    "experiment.json", "result.json", "episode.json", "loop.json", "protocol.json",
    "manifest.json", "summary.json", "comparison.json", "improvement.json",
    "evidence.json", "executor-trace.json", "loop-context.json", "error.json",
}
TEXT_NAMES = {"requests.jsonl", "actions.jsonl", "control.jsonl", "results.jsonl",
              "executor-trace.txt"}
LIMIT = 16 * 1024 * 1024
EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def parts(name):
    if not name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid artifact path")
    raw = name.split("/")
    if any(not p or p.startswith(".") or ":" in p for p in raw):
        raise ValueError("Invalid artifact path")
    return raw


def allowed(name):
    try:
        components = parts(name)
    except ValueError:
        return False
    leaf = components[-1]
    return (leaf in JSON_NAMES or leaf in TEXT_NAMES or leaf == "episode.mp4"
            or bool(re.fullmatch(r"\d+-(agentview|robot0_eye_in_hand)\.png", leaf))
            or (leaf.endswith(".md") and any(p in ("skills", "incumbent") for p in components[:-1])))


def open_artifact(root, name):
    """Use directory-relative opens with O_NOFOLLOW on the supported WSL runtime."""
    components = parts(name)
    if not allowed(name):
        raise FileNotFoundError(name)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in components[:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        target = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        import stat
        if not stat.S_ISREG(os.fstat(target).st_mode):
            os.close(target)
            raise FileNotFoundError(name)
        return os.fdopen(target, "rb")
    finally:
        os.close(fd)


def join(*items):
    return "/".join(str(i).strip("/") for i in items if i)


def sequence(value):
    return value if isinstance(value, list) else []


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def integer(value):
    return value if type(value) is int and value >= 0 else None


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.warnings = []
        self.names = []
        if self.root.is_dir():
            for directory, dirs, files in os.walk(self.root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "inspect"
                                 and not (Path(directory) / d).is_symlink())
                for file in sorted(files):
                    path = Path(directory) / file
                    name = path.relative_to(self.root).as_posix()
                    if not path.is_symlink() and allowed(name):
                        self.names.append(name)
                if len(self.names) > 10000:
                    self.warnings.append("Artifact listing limited to 10,000 files.")
                    self.names = self.names[:10000]
                    break
        self.available = set(self.names)

    def text(self, name):
        if name not in self.available:
            return None
        try:
            with open_artifact(self.root, name) as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw) > LIMIT:
                self.warnings.append(f"{name}: too large to display (16 MiB limit).")
                return None
            return raw.decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            self.warnings.append(f"{name}: temporarily unreadable.")
            return None

    def obj(self, name):
        text = self.text(name)
        if text is None:
            return {}
        try:
            value = json.loads(text, parse_constant=reject_constant)
            if isinstance(value, dict):
                return value
        except (ValueError, RecursionError):
            pass
        self.warnings.append(f"{name}: incomplete or unsupported JSON; waiting for next refresh.")
        return {}

    def lines(self, name):
        text = self.text(name)
        if text is None:
            return [], False
        records, complete = [], True
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line, parse_constant=reject_constant)
                if not isinstance(value, dict):
                    raise ValueError()
                records.append(value)
            except (ValueError, RecursionError):
                complete = False
        if not complete:
            self.warnings.append(f"{name}: partial JSONL; only complete records are shown.")
        return records, complete

    def url(self, name):
        return "/artifacts/" + quote(name, safe="/") if name in self.available else None

    def skills(self, prefix):
        return [{"path": n, "text": self.text(n)} for n in self.names
                if n.startswith(prefix + "/") and n.endswith(".md")]

    def batch(self, prefix, label):
        manifest = self.obj(join(prefix, "manifest.json"))
        summary = self.obj(join(prefix, "summary.json"))
        exp = self.obj(join(prefix, "experiment.json"))
        state_ids = set()
        for name in self.names:
            tail = name[len(prefix) + 1:] if prefix and name.startswith(prefix + "/") else name if not prefix else ""
            match = re.match(r"(?:raw/)?state-(\d{3})/", tail)
            if match:
                state_ids.add(int(match[1]))
        state_ids.update(v for v in sequence(manifest.get("state_ids")) if type(v) is int and v >= 0)
        episodes = []
        for state in sorted(state_ids):
            folder = f"state-{state:03d}"
            ep = join(prefix, folder)
            raw = join(prefix, "raw", folder)
            if not any(n.startswith(ep + "/") for n in self.names):
                ep = join(raw, folder)
            config = self.obj(join(raw, "experiment.json")) or exp
            result = self.obj(join(ep, "result.json"))
            metadata = self.obj(join(ep, "episode.json"))
            controls, _ = self.lines(join(ep, "control.jsonl"))
            req_path = join(raw if prefix else "", "requests.jsonl")
            requests, requests_complete = self.lines(req_path)
            count = integer(result.get("api_requests_attempted"))
            if count is None and requests_complete and all(integer(x.get("request")) is not None for x in requests):
                count = len(requests)
            outcome = "pending"
            if type(result.get("success")) is bool:
                outcome = "success" if result["success"] else "failure"
                if result.get("error"):
                    outcome = "error"
            stills = []
            for name in self.names:
                if PurePosixPath(name).parent.as_posix() != ep:
                    continue
                match = re.fullmatch(r"(\d+)-(agentview|robot0_eye_in_hand)\.png", PurePosixPath(name).name)
                if match:
                    stills.append({"step": int(match[1]), "camera": match[2], "url": self.url(name)})
            stills.sort(key=lambda s: (s["step"], s["camera"]))
            episodes.append({
                "state": state, "path": ep, "outcome": outcome,
                "success": result.get("success") if type(result.get("success")) is bool else None,
                "steps": integer(result.get("steps")), "requests": count,
                "request_records": len(requests) if req_path in self.available else None,
                "termination": result.get("termination"), "error": result.get("error"),
                "instruction": result.get("instruction") or metadata.get("instruction"),
                "model": result.get("requested_model") or config.get("model"),
                "fixture": config.get("smoke") is True or result.get("policy") == "noop",
                "video": self.url(join(ep, "episode.mp4")), "stills": stills,
                "controls": controls[-60:], "control_records": len(controls),
                "result_url": self.url(join(ep, "result.json")),
                "control_url": self.url(join(ep, "control.jsonl")),
                "requests_url": self.url(req_path), "result": result,
            })
        completed = [e for e in episodes if e["success"] is not None]
        successes = sum(e["outcome"] == "success" for e in completed) if completed else None
        def total(key):
            values = [e[key] for e in episodes]
            return sum(values) if values and all(v is not None for v in values) else None
        if summary and (summary.get("episodes") != len(completed) or
                        (successes is not None and summary.get("successes") != successes)):
            self.warnings.append(f"{join(prefix, 'summary.json')}: summary and available episode results differ.")
        return {"path": prefix or ".", "label": label, "manifest": manifest, "summary": summary,
                "episodes": episodes, "completed": len(completed), "expected": len(sequence(manifest.get("state_ids"))) or None,
                "successes": successes, "steps": total("steps"), "requests": total("requests"),
                "skills": self.skills(join(prefix, "skills")), "skill_hash": manifest.get("skill_hash") or exp.get("skill_hash"),
                "fixture": manifest.get("policy") == "noop" or exp.get("smoke") is True
                           or any(e["fixture"] for e in episodes)}


def local_batch(reference, known):
    """Map old absolute paths to recognized local batches after a saved run is copied."""
    if not isinstance(reference, str):
        return None
    normalized = reference.replace("\\", "/").rstrip("/")
    for candidate in sorted(known, key=len, reverse=True):
        if normalized == candidate or normalized.endswith("/" + candidate):
            return candidate
    return None


def snapshot(root, iteration=None):
    r = Reader(root)
    loop = r.obj("loop.json")
    iterations = sorted({n.split("/")[0] for n in r.names if re.match(r"iteration-\d{3}/", n)})
    if iteration is not None and not re.fullmatch(r"iteration-\d{3}", iteration):
        raise ValueError("Invalid iteration")
    selected = iteration if iteration in iterations else iterations[-1] if iterations else None
    is_loop = "loop.json" in r.available or any(n.startswith("baseline/") for n in r.names)
    proposal_base = join(selected, "improvement") if selected else None
    report = r.obj(join(proposal_base, "improvement.json")) if proposal_base else {}
    evidence = r.obj(join(proposal_base, "evidence.json")) if proposal_base else {}
    comparison = r.obj(join(selected, "comparison.json")) if selected else {}
    known = ["baseline"] + [join(i, "candidate") for i in iterations]
    baseline_path = "baseline" if is_loop else ""
    # Infer incumbent only from earlier recorded keep decisions; never from latest loop state.
    if selected:
        for previous in iterations:
            if previous >= selected:
                break
            previous_report = r.obj(join(previous, "comparison.json"))
            if previous_report.get("decision") == "keep":
                baseline_path = join(previous, "candidate")
        baseline_path = local_batch(comparison.get("baseline"), known) or baseline_path
    baseline = r.batch(baseline_path, "Baseline" if baseline_path in ("", "baseline") else "Accepted incumbent")
    candidate = r.batch(join(selected, "candidate"), "Candidate") if selected else None
    tested_files = []
    for name in sequence(evidence.get("tested_skill_files")):
        if isinstance(name, str):
            text = r.text(join(proposal_base, "evidence", name))
            if text is not None:
                tested_files.append({"path": name, "text": text})
    tested_source = "evidence snapshot" if tested_files else "comparison baseline snapshot"
    if not tested_files and not evidence:
        tested_files = baseline["skills"]
    def combine(files):
        return "\n\n".join(f"# {f['path']}\n{f['text']}" if len(files) > 1 else f["text"]
                           for f in files if isinstance(f.get("text"), str))
    tested = combine(tested_files)
    proposed_files = r.skills(join(proposal_base, "skills")) if proposal_base else []
    proposed = report.get("candidate_markdown") if isinstance(report.get("candidate_markdown"), str) else combine(proposed_files)
    proposed = proposed or ""
    diff = "\n".join(difflib.unified_diff(tested.splitlines(), proposed.splitlines(),
                      fromfile="tested skill", tofile="proposed revision", lineterm="")) if proposed else ""
    decision = comparison.get("decision")
    if decision not in ("keep", "reject", "inconclusive"):
        decision = "deferred" if report.get("decision") == "defer" or loop.get("status") == "deferred" else "pending" if is_loop else "not_available"
    stage = 4 if comparison or decision == "deferred" else 3 if candidate and candidate["episodes"] else 2 if report.get("decision") == "propose" else 1 if evidence else 0
    indexed = evidence.get("files_sha256", {})
    evidence_links = [{"name": n, "url": r.url(join(proposal_base, "evidence", n))}
                      for n in indexed if isinstance(n, str)] if isinstance(indexed, dict) else []
    instructions = [e["instruction"] for b in (baseline, candidate) if b for e in b["episodes"] if e["instruction"]]
    fixture = loop.get("fixture") is True or evidence.get("fixture") is True or baseline["fixture"] or bool(candidate and candidate["fixture"])
    return {
        "run_name": Path(root).name, "kind": "loop" if is_loop else "standalone",
        "task": instructions[0] if instructions else "Task not yet recorded",
        "fixture": fixture, "status": loop["status"] if isinstance(loop.get("status"), str) else ("episode_recorded" if baseline["completed"] else "waiting_for_artifacts"),
        "iterations": iterations, "iteration": selected, "stage": stage,
        "baseline": baseline, "candidate": candidate, "decision": decision,
        "comparison": comparison, "diagnosis": report, "evidence": evidence,
        "evidence_links": evidence_links, "skills": {
            "tested": tested, "proposed": proposed, "diff": diff, "tested_source": tested_source,
            "tested_files": tested_files, "proposed_files": proposed_files,
            "no_skill": not evidence and not tested and baseline["skill_hash"] == EMPTY_HASH,
        },
        "warnings": list(dict.fromkeys(r.warnings)),
    }
