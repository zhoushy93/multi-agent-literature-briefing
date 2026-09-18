"""Live client for the OpenAI-compatible chat completions endpoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from briefing.errors import LLMRequestError, LLMTransientError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.llm.budget import BudgetGuard

_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_MAX_BACKOFF_S = 30.0


def default_retry_wait(state: RetryCallState) -> float:
    """Honour Retry-After, otherwise exponential backoff with jitter.

    docs/architecture.md §6.2 lists Retry-After first. Only the
    delta-seconds form is supported; an HTTP-date falls back to backoff.
    """
    outcome = state.outcome
    if outcome is not None:
        try:
            exc = outcome.exception()
        except Exception:  # pragma: no cover - cancelled outcome
            exc = None
        if isinstance(exc, LLMTransientError) and exc.retry_after is not None:
            return min(exc.retry_after, _MAX_BACKOFF_S)
    return wait_exponential_jitter(initial=0.5, max=_MAX_BACKOFF_S)(state)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header in delta-seconds form."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(seconds, 0.0)


class DeepSeekClient:
    """Talks to ``POST {base_url}/chat/completions`` with bounded retries."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        budget: BudgetGuard,
        thinking: str = "auto",
        timeout_s: float = 60.0,
        max_retries: int = 5,
        retry_wait: Callable[[RetryCallState], float] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._thinking = thinking
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._budget = budget
        self._max_retries = max_retries
        self._wait = retry_wait or default_retry_wait
        self._http = http_client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_http = http_client is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        body = self._build_body(request)
        result: LLMResponse | None = None

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._wait,
            retry=retry_if_exception_type(LLMTransientError),
            reraise=True,
        ):
            with attempt:
                result = await self._attempt_once(body, request)

        if result is None:  # pragma: no cover - defensive
            raise LLMTransientError("retry loop finished without a response")
        return result

    # --- internals ----------------------------------------------------------

    def _build_body(self, request: CompletionRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_output:
            body["response_format"] = {"type": "json_object"}
        if self._thinking == "disabled":
            # Verified against the live API: reasoning tokens drop to zero and
            # the reply is no longer at risk of being cut off mid-JSON.
            body["thinking"] = {"type": "disabled"}
        return body

    async def _attempt_once(
        self,
        body: dict[str, Any],
        request: CompletionRequest,
    ) -> LLMResponse:
        self._budget.check()
        try:
            response = await self._http.post(
                self._endpoint,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise LLMTransientError(f"timeout calling the model API: {exc}") from exc
        except httpx.TransportError as exc:
            raise LLMTransientError(f"transport error calling the model API: {exc}") from exc

        self._raise_for_status(response)
        return self._parse_response(response, request)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code in _RETRYABLE_STATUS:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            raise LLMTransientError(
                f"model API returned {response.status_code}",
                retry_after=retry_after,
            )
        if response.status_code >= 400:
            raise LLMRequestError(
                f"model API returned {response.status_code}: {response.text[:200]}"
            )

    def _parse_response(
        self,
        response: httpx.Response,
        request: CompletionRequest,
    ) -> LLMResponse:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            finish_reason = payload["choices"][0].get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError(f"unexpected response shape: {exc}") from exc

        if not isinstance(content, str):
            raise LLMRequestError("model API returned non-string content")

        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        self._budget.record(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            stage=request.stage,
        )
        return LLMResponse(
            text=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
        )
