"""Load `.env` into the process environment.

The Agent SDK runs the Claude Code CLI as a subprocess that inherits `os.environ`,
so putting the credentials there before connecting is all the SDK needs. Kept
dependency-free and deliberately small: `KEY=value` lines, `#` comments, an optional
`export ` prefix, and matched surrounding quotes.

Variables already set in the real environment always win, so an exported shell
variable is never silently overridden by a stale file.
"""

import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".env"


def parse_dotenv(text):
    """Parse dotenv text into a dict, ignoring blank lines and comments."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(path=None, environ=None):
    """Load `path` (default: repo-root `.env`) into `environ`, without overriding.

    Returns the names of the variables it set, so a caller can report what came
    from the file without ever printing a value.
    """
    path = Path(path) if path is not None else DEFAULT_PATH
    environ = os.environ if environ is None else environ
    if not path.is_file():
        return []
    applied = []
    for key, value in parse_dotenv(path.read_text()).items():
        if key in environ:
            continue
        environ[key] = value
        applied.append(key)
    return applied
