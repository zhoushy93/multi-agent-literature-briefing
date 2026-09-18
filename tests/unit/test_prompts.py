"""Prompt file contract tests (docs/architecture.md §9)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import BaseModel

import briefing.schemas as schemas
from briefing.errors import PromptError
from briefing.prompts.loader import DEFAULT_PROMPT_DIR, PromptLoader, parse_prompt

REQUIRED_KEYS = {"agent", "version", "inputs", "output_json_schema", "failure_modes"}

PROMPT_PATHS = sorted(DEFAULT_PROMPT_DIR.glob("*.md"))
PROMPT_IDS = [path.stem for path in PROMPT_PATHS]


def raw_header(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    closing = lines.index("+++", 1)
    return tomllib.loads("\n".join(lines[1:closing]))


@pytest.fixture
def loader() -> PromptLoader:
    return PromptLoader()


def test_at_least_one_prompt_exists() -> None:
    assert PROMPT_PATHS, f"no prompt files under {DEFAULT_PROMPT_DIR}"


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_header_declares_the_required_keys(path: Path) -> None:
    header = raw_header(path)
    assert set(header) >= REQUIRED_KEYS, f"{path.name} is missing {REQUIRED_KEYS - set(header)}"


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_header_values_are_not_empty(path: Path) -> None:
    header = raw_header(path)
    for key in sorted(REQUIRED_KEYS):
        value = header[key]
        assert value, f"{path.name}: {key} is empty"
        if isinstance(value, list):
            assert all(str(item).strip() for item in value), f"{path.name}: blank entry in {key}"


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_version_matches_the_filename(path: Path) -> None:
    """A version is only traceable if the file name and the header agree."""
    assert raw_header(path)["version"] == path.stem


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_version_is_prefixed_by_its_agent(path: Path) -> None:
    header = raw_header(path)
    assert str(header["version"]).startswith(f"{header['agent']}_")


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_header_names_a_real_schema(path: Path) -> None:
    name = str(raw_header(path)["output_json_schema"])
    model = getattr(schemas, name, None)
    assert model is not None, f"{path.name} names unknown schema {name}"
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert name in schemas.__all__


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_placeholders_are_declared_as_inputs(path: Path, loader: PromptLoader) -> None:
    prompt = loader.load(path.stem)
    undeclared = prompt.placeholders() - set(prompt.metadata.inputs)
    assert not undeclared, f"{path.name} uses undeclared placeholders: {sorted(undeclared)}"


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_every_declared_input_is_used(path: Path, loader: PromptLoader) -> None:
    """Catches stale metadata after the body is edited."""
    prompt = loader.load(path.stem)
    unused = set(prompt.metadata.inputs) - prompt.placeholders()
    assert not unused, f"{path.name} declares unused inputs: {sorted(unused)}"


@pytest.mark.parametrize("path", PROMPT_PATHS, ids=PROMPT_IDS)
def test_prompt_never_names_a_model(path: Path) -> None:
    """AGENTS.md §6: the model id is configuration, never prompt text."""
    body = path.read_text(encoding="utf-8").lower()
    assert "deepseek" not in body


def test_render_substitutes_every_placeholder(loader: PromptLoader) -> None:
    prompt = loader.load("planner_v1")
    rendered = prompt.render(
        topic="weather forecasting",
        lang="en",
        available_sources="arxiv",
        time_window="not specified; choose one",
    )
    assert "weather forecasting" in rendered
    assert "$topic" not in rendered


def test_render_raises_on_a_missing_input(loader: PromptLoader) -> None:
    prompt = loader.load("planner_v1")
    with pytest.raises(KeyError):
        prompt.render(topic="only one value")


def test_loader_reports_a_missing_prompt(loader: PromptLoader) -> None:
    with pytest.raises(PromptError, match="not found"):
        loader.load("does_not_exist_v1")


def test_loader_rejects_a_filename_version_mismatch(tmp_path: Path) -> None:
    (tmp_path / "planner_v9.md").write_text(
        '+++\nagent = "planner"\nversion = "planner_v1"\n'
        'inputs = ["topic"]\noutput_json_schema = "SearchPlan"\n'
        'failure_modes = ["x"]\n+++\n\nBody $topic\n',
        encoding="utf-8",
    )
    with pytest.raises(PromptError, match="declares version"):
        PromptLoader(tmp_path).load("planner_v9")


def test_loader_caches_parsed_prompts(loader: PromptLoader) -> None:
    assert loader.load("planner_v1") is loader.load("planner_v1")


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("no front matter at all", "must start with"),
        ('+++\nagent = "a"\n\nbody', "never closed"),
        ('+++\nagent = "a"\n+++\n\n   ', "body is empty"),
        ('+++\nagent = "a"\nversion = 1\n+++\n\nbody', "invalid prompt front matter"),
    ],
)
def test_parse_rejects_malformed_prompts(text: str, match: str) -> None:
    with pytest.raises(PromptError, match=match):
        parse_prompt(text)


def test_planner_prompt_demands_english_queries(loader: PromptLoader) -> None:
    """The only offline guarantee that the bilingual rule survives edits."""
    body = loader.load("planner_v1").template.lower()
    assert "in english" in body
    assert "original-language" in body
