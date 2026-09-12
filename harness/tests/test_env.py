"""Tests for the dotenv loader."""

from harness.env import load_dotenv, parse_dotenv


def test_parses_comments_blank_lines_export_and_quotes():
    text = "\n".join(
        [
            "# a comment",
            "",
            "ANTHROPIC_API_KEY=sk-ant-value",
            "export MUJOCO_GL=osmesa",
            'QUOTED="spaced value"',
            "SINGLE='single'",
            "  PADDED = padded ",
            "NOT_AN_ASSIGNMENT",
        ]
    )
    assert parse_dotenv(text) == {
        "ANTHROPIC_API_KEY": "sk-ant-value",
        "MUJOCO_GL": "osmesa",
        "QUOTED": "spaced value",
        "SINGLE": "single",
        "PADDED": "padded",
    }


def test_values_containing_equals_survive():
    assert parse_dotenv("TOKEN=a=b=c") == {"TOKEN": "a=b=c"}


def test_load_sets_missing_and_reports_names(tmp_path):
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=from-file\nMUJOCO_GL=osmesa\n")
    environ = {}

    assert sorted(load_dotenv(path, environ)) == ["ANTHROPIC_API_KEY", "MUJOCO_GL"]
    assert environ["ANTHROPIC_API_KEY"] == "from-file"


def test_existing_environment_wins(tmp_path):
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=from-file\n")
    environ = {"ANTHROPIC_API_KEY": "from-shell"}

    assert load_dotenv(path, environ) == []
    assert environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_missing_file_is_not_an_error(tmp_path):
    assert load_dotenv(tmp_path / "absent", {}) == []
