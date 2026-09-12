"""Fetch pinned upstream assets; runs with stdlib before `uv sync`."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBERO_ROOT = ROOT / ".vendor" / "libero"
LIBERO_REV = "8f1084e3132a39270c3a13ebe37270a43ece2a01"
LIBERO_URL = "https://github.com/Lifelong-Robot-Learning/LIBERO.git"


def configure() -> None:
    """Keep LIBERO's import-time configuration inside this checkout."""
    source = LIBERO_ROOT / "libero" / "libero"
    if not (source / "assets").is_dir():
        raise RuntimeError("Missing LIBERO assets. Run: uv run --no-project simulation/prepare.py")
    config = ROOT / ".libero"
    config.mkdir(exist_ok=True)
    datasets = LIBERO_ROOT / "libero" / "datasets"
    datasets.mkdir(exist_ok=True)
    paths = {
        "benchmark_root": str(source),
        "bddl_files": str(source / "bddl_files"),
        "init_states": str(source / "init_files"),
        "assets": str(source / "assets"),
        "datasets": str(datasets),
    }
    # JSON is valid YAML; avoids needing PyYAML before installation.
    (config / "config.yaml").write_text(json.dumps(paths, indent=2) + "\n")
    os.environ["LIBERO_CONFIG_PATH"] = str(config)
    # Upstream's find_packages() omits its outer namespace package under modern
    # setuptools. Import the pinned source directly without editing upstream.
    if str(LIBERO_ROOT) not in sys.path:
        sys.path.insert(0, str(LIBERO_ROOT))


def main() -> None:
    if not LIBERO_ROOT.exists():
        LIBERO_ROOT.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "init", str(LIBERO_ROOT)], check=True)
        subprocess.run(
            ["git", "-C", str(LIBERO_ROOT), "remote", "add", "origin", LIBERO_URL], check=True
        )
        subprocess.run(
            ["git", "-C", str(LIBERO_ROOT), "fetch", "--depth", "1", "origin", LIBERO_REV],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(LIBERO_ROOT), "checkout", "--detach", LIBERO_REV], check=True
        )
    revision = subprocess.check_output(
        ["git", "-C", str(LIBERO_ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != LIBERO_REV:
        raise RuntimeError(
            f"Expected LIBERO {LIBERO_REV}, found {revision}; not overwriting checkout"
        )
    configure()
    print(f"LIBERO ready at {LIBERO_ROOT}\nNext: uv sync --locked")


if __name__ == "__main__":
    main()
