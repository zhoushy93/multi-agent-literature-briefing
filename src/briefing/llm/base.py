"""The LLM boundary.

Business code depends on ``LLMClient`` and never on a vendor SDK (AGENTS.md
§2.2), so the live client and the offline stub are interchangeable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from briefing.errors import LLMRequestError, SchemaValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)

# Per-reply ceiling used when a caller does not set one. Configuration can
# raise it (BRIEFING_MAX_OUTPUT_TOKENS).
DEFAULT_MAX_TOKENS = 4096


@dataclass(frozen=True)
class CompletionRequest:
    """One stateless LLM call. No conversation history is ever carried over."""

    system: str
    user: str
    temperature: float = 0.0
    max_tokens: int = DEFAULT_MAX_TOKENS
    json_output: bool = True
    stage: str | None = None
    prompt_version: str | None = None

    def with_feedback(self, feedback: str) -> CompletionRequest:
        """Return a copy with repair instructions appended to the user turn."""
        return replace(self, user=f"{self.user}\n\n{feedback}")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # "stop" when the model finished, "length" when it was cut off. Keeping it
    # makes a truncated reply diagnosable instead of a puzzling JSON error.
    finish_reason: str | None = None


class LLMClient(Protocol):
    """The only LLM surface the agents may touch."""

    async def complete(self, request: CompletionRequest) -> LLMResponse: ...


def extract_json(text: str) -> str:
    """Strip a Markdown code fence if the model wrapped its JSON."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    parts = stripped.split("```")
    if len(parts) < 2:
        return stripped
    body = parts[1]
    if body.startswith("json"):
        body = body[4:]
    return body.strip()


async def complete_structured(
    client: LLMClient,
    request: CompletionRequest,
    schema: type[ModelT],
    *,
    max_schema_retries: int = 2,
    post_validate: Callable[[ModelT], ModelT] | None = None,
) -> ModelT:
    """Call the model until its reply validates against ``schema``.

    AGENTS.md §6: a validation failure is retried at most twice, and the retry
    carries the original validation error so the model can repair itself.

    ``post_validate`` runs inside the same retry loop for checks that need
    context the schema cannot see (for example "this quote must appear in the
    source text"). Raising ``ValueError`` from it sends the message back to the
    model as repair instructions; other exceptions propagate untouched.
    """
    feedback: str | None = None
    last_error: Exception | None = None

    for _ in range(max_schema_retries + 1):
        current = request if feedback is None else request.with_feedback(feedback)
        response = await client.complete(current)
        if response.finish_reason == "length":
            # The same prompt truncates the same way, so retrying only spends
            # money. Fail with something the operator can act on.
            raise LLMRequestError(
                "the model stopped early (finish_reason=length): the reply was cut off "
                f"after {response.completion_tokens} completion tokens. "
                "Raise BRIEFING_MAX_OUTPUT_TOKENS, or send a smaller prompt "
                "(see BRIEFING_MAX_CANDIDATES)."
            )
        try:
            # Parse first so that malformed JSON and schema mismatches produce
            # different repair instructions. model_validate_json would collapse
            # both into a ValidationError and the JSON branch below would be dead.
            parsed = schema.model_validate(parse_json_object(response.text))
        except ValidationError as exc:
            last_error = exc
            feedback = (
                "Your previous reply was rejected by the schema validator.\n"
                f"Validation errors:\n{exc}\n"
                f"Return only a JSON object matching {schema.__name__}."
            )
            continue
        except ValueError as exc:  # malformed JSON, or not a JSON object
            last_error = exc
            feedback = (
                f"Your previous reply was not valid JSON: {exc}\n"
                f"Return only a JSON object matching {schema.__name__}."
            )
            continue

        if post_validate is None:
            return parsed
        try:
            return post_validate(parsed)
        except ValueError as exc:
            last_error = exc
            feedback = str(exc)

    raise SchemaValidationError(
        f"{schema.__name__} was not satisfied after {max_schema_retries + 1} attempts: {last_error}"
    ) from last_error


def parse_json_object(text: str) -> dict[str, object]:
    """Parse a JSON object, raising ``ValueError`` when it is not one."""
    data = json.loads(extract_json(text))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data
