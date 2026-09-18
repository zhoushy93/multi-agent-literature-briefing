"""Prompt loading and versioning (docs/architecture.md §9).

Every prompt is a Markdown file with a TOML front matter block, so metadata is
parsed with the standard library instead of a hand-rolled parser or a new
dependency::

    +++
    agent = "planner"
    version = "planner_v1"
    inputs = ["topic", "lang"]
    output_json_schema = "SearchPlan"
    failure_modes = ["fewer than two queries"]
    +++

    Task instructions, with $topic placeholders.

The file name, the ``version`` field and the version recorded in
``manifest.json`` are all the same string, so a run can always be traced back
to the exact prompt text that produced it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from string import Template

from pydantic import Field, ValidationError

from briefing.errors import PromptError
from briefing.schemas import Contract

FRONT_MATTER_DELIMITER = "+++"
DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent


class PromptMetadata(Contract):
    """The front matter block of a prompt file."""

    agent: str = Field(min_length=1)
    version: str = Field(min_length=1)
    inputs: list[str] = Field(min_length=1)
    output_json_schema: str = Field(min_length=1)
    failure_modes: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class Prompt:
    """A parsed prompt: metadata plus a substitutable body."""

    metadata: PromptMetadata
    template: str

    @property
    def version(self) -> str:
        return self.metadata.version

    def placeholders(self) -> set[str]:
        return set(Template(self.template).get_identifiers())

    def render(self, **values: str) -> str:
        """Substitute ``$name`` placeholders; missing values raise ``KeyError``."""
        return Template(self.template).substitute(**values)


def parse_prompt(text: str) -> Prompt:
    """Parse a prompt file's contents."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        raise PromptError("prompt file must start with a +++ front matter block")

    closing = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == FRONT_MATTER_DELIMITER),
        None,
    )
    if closing is None:
        raise PromptError("prompt front matter block is never closed")

    header = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1 :]).strip()
    if not body:
        raise PromptError("prompt body is empty")

    try:
        metadata = PromptMetadata.model_validate(tomllib.loads(header))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise PromptError(f"invalid prompt front matter: {exc}") from exc

    return Prompt(metadata=metadata, template=body)


class PromptLoader:
    """Reads prompt files from a directory, caching what it has parsed."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or DEFAULT_PROMPT_DIR
        self._cache: dict[str, Prompt] = {}

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, name: str) -> Path:
        return self._directory / f"{name}.md"

    def load(self, name: str) -> Prompt:
        """Load ``<name>.md``, e.g. ``planner_v1``."""
        cached = self._cache.get(name)
        if cached is not None:
            return cached

        path = self.path_for(name)
        if not path.is_file():
            raise PromptError(f"prompt not found: {path}")

        prompt = parse_prompt(path.read_text(encoding="utf-8"))
        if prompt.metadata.version != name:
            raise PromptError(f"prompt {path.name} declares version {prompt.metadata.version!r}")
        self._cache[name] = prompt
        return prompt

    def load_all(self) -> dict[str, Prompt]:
        """Parse every prompt file in the directory, keyed by stem."""
        return {path.stem: self.load(path.stem) for path in sorted(self._directory.glob("*.md"))}
