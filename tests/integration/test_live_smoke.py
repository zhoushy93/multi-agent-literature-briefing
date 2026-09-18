"""Live smoke tests: the only tests that touch the real APIs.

Every test here is skipped unless ``RUN_LIVE_TESTS=1`` and an API key is
configured, so an ordinary ``uv run pytest`` never calls a paid endpoint
(AGENTS.md §10).

The full pipeline run costs several model calls, so it needs a second opt-in,
``RUN_LIVE_E2E=1``, on top of the first. That keeps "check the API still works"
cheap and makes "run the whole thing for real" a deliberate choice.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
import pytest

from briefing.cli import main
from briefing.config import Settings
from briefing.llm import create_llm_client
from briefing.llm.base import CompletionRequest, complete_structured
from briefing.llm.budget import BudgetGuard
from briefing.schemas import Contract

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.live

RUN_LIVE = os.environ.get("RUN_LIVE_TESTS") == "1"

EXPECTED_E2E_CALLS = (7, 40)


def configured_settings() -> Settings:
    return Settings()


def has_api_key() -> bool:
    try:
        return bool(configured_settings().deepseek_api_key)
    except Exception:  # pragma: no cover - a broken .env should skip, not error
        return False


LIVE_READY = RUN_LIVE and has_api_key()

requires_live = pytest.mark.skipif(
    not LIVE_READY,
    reason="set RUN_LIVE_TESTS=1 and DEEPSEEK_API_KEY (or .env) to run live checks",
)
requires_full_run = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_E2E") != "1",
    reason="set RUN_LIVE_E2E=1 as well: the full pipeline spends several model calls",
)


class ArithmeticAnswer(Contract):
    """The smallest possible structured reply."""

    sum: int


@requires_live
def test_live_model_ids_are_available() -> None:
    """AGENTS.md §14: confirm the configured model id exists, do not assume it."""
    settings = configured_settings()
    response = httpx.get(
        f"{settings.deepseek_base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
        timeout=30.0,
    )
    assert response.status_code == 200, response.text[:200]

    models = [entry["id"] for entry in response.json().get("data", [])]
    logger.info("available model ids: %s", ", ".join(models))
    assert models, "the models endpoint returned no ids"
    assert settings.deepseek_model in models, (
        f"{settings.deepseek_model!r} is not offered; available: {', '.join(models)}"
    )


@requires_live
async def test_live_structured_call_returns_a_validated_object() -> None:
    """Proves JSON mode, schema validation and usage parsing against the real API."""
    settings = configured_settings()
    budget = BudgetGuard(max_calls=5, max_tokens=20_000)
    client = create_llm_client(settings, budget)
    try:
        answer = await complete_structured(
            client,
            CompletionRequest(
                system="You reply with a single JSON object and nothing else.",
                user="Return a JSON object whose field 'sum' is the result of 2 + 3.",
                temperature=0.0,
                stage="live-smoke",
                prompt_version=None,
            ),
            ArithmeticAnswer,
            max_schema_retries=1,
        )
    finally:
        close = getattr(client, "aclose", None)
        if callable(close):
            await close()

    assert answer.sum == 5
    snapshot = budget.snapshot()
    assert snapshot.calls == 1
    assert snapshot.total_tokens > 0, "usage must be parsed from the real response"


@requires_live
@requires_full_run
def test_live_full_pipeline_produces_a_briefing(tmp_path: Path) -> None:
    """The real thing: live arXiv retrieval plus live model calls."""
    out_dir = tmp_path / "live"
    exit_code = main(
        [
            "run",
            "--topic",
            "diffusion models for weather forecasting",
            "--out",
            str(out_dir),
            "--no-cache",
        ]
    )
    assert exit_code == 0

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok", manifest.get("error")
    assert manifest["model"]["id"] == configured_settings().deepseek_model
    low, high = EXPECTED_E2E_CALLS
    assert low <= manifest["llm_usage"]["calls"] <= high
    assert manifest["llm_usage"]["prompt_tokens"] > 0
    assert 3 <= manifest["retrieval"]["selected"] <= 5
    assert manifest["retrieval"]["queries"], "the run must record what it searched for"
    assert manifest["renderer"]["name"] == "weasyprint"

    for name in ("report.pdf", "briefing.md", "briefing.json", "papers.json"):
        assert (out_dir / name).is_file(), name

    # Live runs are not reproducible, so the record of what happened matters.
    assert manifest["verification"]["passed"] is True
    assert set(manifest["prompt_versions"]) == {
        "planner_v1",
        "screener_v1",
        "analyzer_v2",
        "synthesizer_v2",
        "verifier_v1",
    }
