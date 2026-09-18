"""Guard: nothing credential-shaped may reach a commit.

GitHub receives exactly what is in the index, so this test scans that set —
tracked files plus untracked files that are not ignored — and fails on any
credential-shaped string. Because it runs in the normal gate, this repository
cannot hold a key and still pass its own test suite.

The patterns are deliberately narrow: they must catch a real key while never
tripping on the placeholders and fake values the tests themselves use.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# (pattern, what it would be) — kept tight to avoid false positives on test data.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sk-[A-Za-z0-9]{20,}", "an OpenAI/DeepSeek style API key"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "a GitHub token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}", "a bearer token"),
    (
        r"DEEPSEEK_API_KEY\s*[:=]\s*['\"]?[A-Za-z0-9_-]{20,}",
        "a real DEEPSEEK_API_KEY value",
    ),
    (
        r"(?i)(api[_-]?key|apikey|secret|access[_-]?token|password)"
        r"\s*[:=]\s*['\"][A-Za-z0-9/+=_-]{24,}['\"]",
        "a hard-coded credential",
    ),
)

FORBIDDEN_IN_INDEX = (".env", ".env.local", "secrets/keys.json", "credentials.json")

IGNORED_PATHS = (
    ".env",
    ".env.local",
    "data/cache/arxiv/deadbeef.json",
    "outputs/run/report.pdf",
    "scratch/notes.md",
    "secrets/keys.json",
    "server.pem",
)

BINARY_SUFFIXES = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".whl", ".pyc", ".ttc"}
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout


def files_that_would_be_uploaded() -> list[Path]:
    tracked = git("ls-files").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    return [REPO_ROOT / name for name in sorted(set(tracked) | set(untracked))]


def test_no_credential_shaped_string_is_committable() -> None:
    hits: list[str] = []
    for path in files_that_would_be_uploaded():
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(f"{path.relative_to(REPO_ROOT)} looks like it contains {label}")
    assert not hits, "credential-shaped content found:\n" + "\n".join(sorted(set(hits)))


@pytest.mark.parametrize("path", IGNORED_PATHS)
def test_secret_bearing_paths_are_ignored(path: str) -> None:
    """Ignoring is what stops a stray `git add -A` from publishing a key."""
    assert git("check-ignore", path).strip() == path


@pytest.mark.parametrize("path", FORBIDDEN_IN_INDEX)
def test_no_secret_file_is_in_the_index(path: str) -> None:
    tracked = git("ls-files").splitlines()
    assert path not in tracked, f"{path} is tracked and would be uploaded"


def test_the_example_env_file_is_still_tracked() -> None:
    """The template belongs in the repository; only its values do not."""
    assert ".env.example" in git("ls-files").splitlines()


CREDENTIAL_NAMES = frozenset(
    {"DEEPSEEK_API_KEY", "BRIEFING_DEEPSEEK_API_KEY", "BRIEFING_DEEPSEEK_TOKEN"}
)


def test_the_example_env_file_holds_no_credential_values() -> None:
    """Defaults are fine here; a credential is not."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        seen.add(name.strip())
        if name.strip() in CREDENTIAL_NAMES:
            assert not value.strip(), f"{name} has a value in .env.example"
    assert seen & CREDENTIAL_NAMES, "the example must still document the API key"
