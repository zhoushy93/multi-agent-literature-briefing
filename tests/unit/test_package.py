from __future__ import annotations

import pytest

import briefing
from briefing.cli import EXIT_USAGE, main


def test_version_is_exposed() -> None:
    assert briefing.__version__ == "0.1.0"


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0


def test_missing_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_run_rejects_a_topic_that_is_too_short() -> None:
    """The CLI validates the request before spending anything (AGENTS.md §9)."""
    assert main(["run", "--topic", "x", "--out", "/tmp/x"]) == EXIT_USAGE
