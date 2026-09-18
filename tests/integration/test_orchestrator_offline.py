"""End-to-end orchestrator tests: fake source, scripted model, real PDF engine.

The scripted model reads *only* what the prompt shows it, exactly like a real
model would. That is what makes these tests able to catch a prompt that is
missing information the response schema demands.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from briefing.cli import EXIT_USAGE, main, make_run_id
from briefing.config import Settings
from briefing.errors import RenderError, SourceError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.llm.budget import BudgetGuard
from briefing.manifest import STAGES
from briefing.orchestrator import Orchestrator
from briefing.schemas import Paper, TopicRequest
from briefing.sources.base import FetchParams
from briefing.sources.cache import CacheStore

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
FROZEN_NOW = datetime(2026, 9, 17, 13, 0, tzinfo=UTC)
PAPER_IDS = ("arxiv:2401.00001", "arxiv:2401.00002", "arxiv:2401.00003")


def make_paper(index: int) -> Paper:
    return Paper(
        paper_id=PAPER_IDS[index - 1],
        title=f"Diffusion Study {index}",
        authors=[f"Alice Author {index}"],
        year=2024,
        venue="Journal of Weather ML",
        origin="arxiv",
        url=f"https://arxiv.org/abs/{PAPER_IDS[index - 1].split(':')[1]}",
        arxiv_id=PAPER_IDS[index - 1].split(":")[1],
        abstract=f"We study diffusion models number {index} for weather forecasting on ERA5.",
        retrieved_at=RETRIEVED_AT,
    )


class FakeSource:
    """A source that returns a fixed list, ignoring the query."""

    name = "arxiv"

    def __init__(self, papers: list[Paper]) -> None:
        self._papers = papers
        self.dropped: dict[str, int] = {}

    async def search(self, query: str, params: FetchParams) -> list[Paper]:
        return list(self._papers)


class FailingSource:
    name = "arxiv"

    def __init__(self) -> None:
        self.dropped: dict[str, int] = {}

    async def search(self, query: str, params: FetchParams) -> list[Paper]:
        raise SourceError("arxiv is down")


class ScriptedLLM:
    """Answers from the prompt text, the way a cooperating model would."""

    def __init__(
        self,
        *,
        budget: BudgetGuard | None = None,
        comparison_rows: int | None = None,
        analyzer_quote: str | None = None,
    ) -> None:
        self.budget = budget
        self.comparison_rows = comparison_rows
        self.analyzer_quote = analyzer_quote
        self.calls: list[str] = []
        self.allowed_calls: set[str] | None = None

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        if self.allowed_calls is not None and request.prompt_version not in self.allowed_calls:
            raise AssertionError(f"unexpected call to {request.prompt_version}")
        if self.budget is not None:
            self.budget.check()
        self.calls.append(request.prompt_version or "unknown")
        text = self._reply(request)
        if self.budget is not None:
            self.budget.record(prompt_tokens=100, completion_tokens=50, stage=request.stage)
        return LLMResponse(text=text, prompt_tokens=100, completion_tokens=50)

    def _reply(self, request: CompletionRequest) -> str:
        version = request.prompt_version
        if version == "planner_v1":
            return json.dumps(
                {
                    "normalized_topic": "diffusion models for weather forecasting",
                    "queries": [
                        {
                            "source": "arxiv",
                            "q": 'all:"diffusion model" AND all:forecasting',
                            "rationale": "core terms",
                            "weight": 1.0,
                        },
                        {
                            "source": "arxiv",
                            "q": "abs:score-based",
                            "rationale": "method family",
                            "weight": 0.6,
                        },
                    ],
                    "keywords": ["diffusion", "forecasting", "generative"],
                    "inclusion_criteria": ["proposes a generative forecasting model"],
                    "exclusion_criteria": ["is not peer reviewed"],
                    "time_window": [None, None],
                }
            )
        if version == "screener_v1":
            tags = re.findall(r"^(C\d+)(?: \[short-abstract\])?:", request.user, re.MULTILINE)
            return json.dumps(
                {
                    "selected": [
                        {
                            "tag": tag,
                            "rank": rank,
                            "relevance_score": 0.9,
                            "rationale": "matches the topic",
                            "matched_inclusion": ["proposes a generative forecasting model"],
                            "matched_exclusion": [],
                        }
                        for rank, tag in enumerate(tags[:3], start=1)
                    ],
                    "selection_notes": "three candidates selected",
                }
            )
        if version == "analyzer_v2":
            paper_id = _search(r"paper_id: (\S+)", request.user)
            abstract = _search(r"abstract:\n(.+)", request.user).split(". ")[0]
            quote = self.analyzer_quote or abstract
            return json.dumps(
                {
                    "paper_id": paper_id,
                    "problem": "Long-horizon forecasts degrade.",
                    "method": "A conditional diffusion model.",
                    "data_and_experiments": "ERA5 reanalysis.",
                    "key_findings": ["Lower RMSE at seven days."],
                    "limitations": ["One region only."],
                    "relevance": "Relevant because it forecasts weather.",
                    "reusable_ideas": ["Noise annealing"],
                    "evidence": [
                        {
                            "field": "abstract",
                            "locator": "abstract[0:40]",
                            "quote": quote,
                        }
                    ],
                    "confidence": 0.7,
                }
            )
        if version == "synthesizer_v2":
            return self._briefing(request.user)
        raise AssertionError(f"the script has no reply for {version}")

    def _briefing(self, prompt: str) -> str:
        keys = re.findall(r"^\[(P\d+)\] ", prompt, re.MULTILINE)
        rows = keys if self.comparison_rows is None else keys[: self.comparison_rows]
        return json.dumps(
            {
                "run_id": _search(r"run_id: (\S+)", prompt),
                "topic": _search(r"topic: (.+)", prompt),
                "lang": _search(r"report language: (\w+)", prompt),
                "executive_summary": "Diffusion models improve long-range weather forecasts.",
                "background_md": "## Background\n\nDiffusion models entered weather [P1].",
                "method_md": "## Method\n\nWe searched arXiv [P2].",
                "comparison": [
                    {
                        "citation_key": key,
                        "task": "Forecasting",
                        "method_family": "Diffusion",
                        "data": "ERA5",
                        "metrics": "RMSE",
                        "main_result": "Lower error",
                    }
                    for key in rows
                ],
                "gaps_and_open_questions": [
                    {"text": "Regional transfer is untested.", "citation_keys": [keys[0]]}
                ],
                "further_reading": [
                    {"text": "Start with the newest paper.", "citation_keys": [keys[1]]}
                ],
            }
        )


def _search(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _analysis(paper_id: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "problem": "Long-horizon forecasts degrade.",
        "method": "A conditional diffusion model.",
        "data_and_experiments": "ERA5 reanalysis.",
        "key_findings": ["Lower RMSE at seven days."],
        "limitations": ["One region only."],
        "relevance": "Relevant because it forecasts weather.",
        "reusable_ideas": [],
        "evidence": [
            {
                "field": "abstract",
                "locator": "abstract[0:40]",
                "quote": "We study diffusion models",
            }
        ],
        "confidence": 0.7,
    }


def make_request(tmp: Path, **overrides: Any) -> TopicRequest:
    data: dict[str, Any] = {
        "topic": "diffusion models for weather forecasting",
        "out_dir": tmp / "run",
        "run_id": "20260917T130000Z-diffusion",
        "resume": False,
        "no_cache": True,
    }
    data.update(overrides)
    return TopicRequest(**data)


async def run_pipeline(
    tmp: Path,
    *,
    papers: list[Paper] | None = None,
    llm: ScriptedLLM | None = None,
    settings: Settings | None = None,
    engines: Any = None,
    request: TopicRequest | None = None,
    source: Any = None,
) -> tuple[Any, ScriptedLLM]:
    request = request or make_request(tmp)
    settings = settings or Settings(_env_file=None)
    budget = BudgetGuard(
        max_calls=settings.max_llm_calls,
        max_tokens=settings.max_tokens_budget,
    )
    scripted = llm or ScriptedLLM(budget=budget)
    orchestrator = Orchestrator(
        request=request,
        settings=settings,
        llm=scripted,
        sources=[source or FakeSource(papers or [make_paper(i) for i in (1, 2, 3)])],
        cache=CacheStore(root=tmp / "cache", enabled=not request.no_cache),
        budget=budget,
        engines=engines,
        clock=lambda: FROZEN_NOW,
    )
    return await orchestrator.run(), scripted


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, ScriptedLLM, Path]:
    tmp = tmp_path_factory.mktemp("completed")
    manifest, llm = asyncio_run(run_pipeline(tmp))
    return manifest, llm, tmp / "run"


def asyncio_run(coroutine: Any) -> Any:
    import asyncio

    return asyncio.run(coroutine)


# --- the happy path ----------------------------------------------------------


def test_a_full_run_succeeds(completed_run: tuple[Any, ScriptedLLM, Path]) -> None:
    manifest, _, _ = completed_run
    assert manifest.status == "ok"
    assert manifest.retrieval.selected == 3
    # Two queries, and the fake source answers both with the same three papers.
    assert manifest.retrieval.candidates_found == 6
    assert manifest.retrieval.after_dedup == 3, "the normalizer collapses the repeats"
    assert manifest.retrieval.per_source_counts == {"arxiv": 6}


def test_every_artifact_is_written(completed_run: tuple[Any, ScriptedLLM, Path]) -> None:
    _, _, out_dir = completed_run
    for name in ("report.pdf", "briefing.md", "briefing.json", "papers.json", "manifest.json"):
        path = out_dir / name
        assert path.is_file(), f"missing artifact {name}"
        assert path.stat().st_size > 0


def test_stage_checkpoints_are_written(completed_run: tuple[Any, ScriptedLLM, Path]) -> None:
    _, _, out_dir = completed_run
    for stage in STAGES:
        assert (out_dir / "_stages" / f"{stage}.json").is_file(), stage
    assert (out_dir / "_stages" / "08_report.html").is_file()


def test_briefing_markdown_mirrors_the_briefing(
    completed_run: tuple[Any, ScriptedLLM, Path],
) -> None:
    _, _, out_dir = completed_run
    markdown = (out_dir / "briefing.md").read_text(encoding="utf-8")
    briefing = json.loads((out_dir / "briefing.json").read_text(encoding="utf-8"))
    assert briefing["executive_summary"] in markdown
    assert "## References" in markdown
    assert "[1]" in markdown


def test_papers_json_carries_metadata_and_analyses(
    completed_run: tuple[Any, ScriptedLLM, Path],
) -> None:
    _, _, out_dir = completed_run
    payload = json.loads((out_dir / "papers.json").read_text(encoding="utf-8"))
    assert [record["screened"]["paper"]["paper_id"] for record in payload["papers"]] == list(
        PAPER_IDS
    )
    assert all(record["analysis"]["evidence"] for record in payload["papers"])


# --- the manifest ------------------------------------------------------------


def test_manifest_records_every_required_field(
    completed_run: tuple[Any, ScriptedLLM, Path],
) -> None:
    manifest, _, _ = completed_run
    assert manifest.run_id == "20260917T130000Z-diffusion"
    assert manifest.model.id == "deepseek-v4-pro"
    assert manifest.model.params.temperature["analyze"] == 0.2
    assert set(manifest.prompt_versions) == {
        "planner_v1",
        "screener_v1",
        "analyzer_v2",
        "synthesizer_v2",
        "verifier_v1",
    }
    assert manifest.verification.passed is True
    assert manifest.verification.semantic_enabled is False
    assert manifest.renderer.name == "weasyprint"
    assert manifest.renderer.fallback is False
    assert manifest.renderer.cjk_font_embedded is True
    assert manifest.error is None


def test_manifest_counts_calls_per_stage(
    completed_run: tuple[Any, ScriptedLLM, Path],
) -> None:
    manifest, llm, _ = completed_run
    assert manifest.llm_usage.calls == len(llm.calls)
    assert manifest.llm_usage.per_stage["S5"] == 3, "one analyzer call per paper"
    assert manifest.llm_usage.per_stage["S1"] == 1
    assert manifest.llm_usage.prompt_tokens > 0
    assert manifest.llm_usage.est_cost_usd is None


def test_manifest_is_valid_json_on_disk(
    completed_run: tuple[Any, ScriptedLLM, Path],
) -> None:
    _, _, out_dir = completed_run
    payload = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert set(payload["artifacts"]) == {
        "report_pdf",
        "briefing_md",
        "briefing_json",
        "papers_json",
    }


def test_stage_timings_and_resume_are_recorded(
    completed_run: tuple[Any, ScriptedLLM, Path],
) -> None:
    manifest, _, _ = completed_run
    assert {"S1", "S2", "S3", "S4", "S5", "S8"} <= set(manifest.stage_timings_s)
    assert manifest.resume.enabled is False
    assert manifest.resume.skipped_stages == []


# --- failure paths -----------------------------------------------------------


class BrokenEngine:
    name = "broken"

    def render(self, html: str, target: Path, *, html_path: Path) -> None:
        raise RenderError("engine exploded")


async def test_insufficient_papers_status(tmp_path: Path) -> None:
    manifest, _ = await run_pipeline(
        tmp_path,
        papers=[make_paper(1), make_paper(2)],
    )
    assert manifest.status == "insufficient_papers"
    assert manifest.error is not None
    assert "only 2 usable paper" in manifest.error.message
    assert "widen the topic" in manifest.error.message


async def test_analysis_failed_status(tmp_path: Path) -> None:
    budget = BudgetGuard(max_calls=40, max_tokens=400_000)
    manifest, _ = await run_pipeline(
        tmp_path,
        llm=ScriptedLLM(budget=budget, analyzer_quote="A sentence that is not in the paper"),
    )
    assert manifest.status == "analysis_failed"
    assert manifest.error is not None
    assert "could not be analysed" in manifest.error.message


async def test_verification_failed_status(tmp_path: Path) -> None:
    """A row-count breach survives the revision, so the run ends."""
    budget = BudgetGuard(max_calls=40, max_tokens=400_000)
    llm = ScriptedLLM(budget=budget, comparison_rows=1)
    manifest, _ = await run_pipeline(tmp_path, llm=llm)
    assert manifest.status == "verification_failed"
    assert manifest.error is not None
    assert "survived the revision" in manifest.error.message
    assert manifest.verification.passed is False
    assert manifest.verification.revisions == 1


async def test_the_revision_happens_exactly_once(tmp_path: Path) -> None:
    budget = BudgetGuard(max_calls=40, max_tokens=400_000)
    llm = ScriptedLLM(budget=budget, comparison_rows=1)
    await run_pipeline(tmp_path, llm=llm)
    assert llm.calls.count("synthesizer_v2") == 2, "one draft plus one revision"
    revision_prompt = [call for call in llm.calls if call == "synthesizer_v2"]
    assert len(revision_prompt) == 2


async def test_budget_exceeded_status(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_llm_calls=1)
    manifest, _ = await run_pipeline(tmp_path, settings=settings)
    assert manifest.status == "budget_exceeded"
    assert manifest.budget.exceeded is True
    assert manifest.budget.reason is not None
    assert manifest.llm_usage.calls == 1


async def test_render_failed_status(tmp_path: Path) -> None:
    manifest, _ = await run_pipeline(tmp_path, engines=[BrokenEngine()])
    assert manifest.status == "render_failed"
    assert manifest.error is not None
    assert "no pdf engine" in manifest.error.message


async def test_a_broken_source_is_a_run_error(tmp_path: Path) -> None:
    manifest, _ = await run_pipeline(tmp_path, source=FailingSource())
    assert manifest.status == "run_error"
    assert manifest.error is not None
    assert manifest.error.type == "SourceError"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"papers": [make_paper(1), make_paper(2)]},
        {"engines": [BrokenEngine()]},
    ],
)
async def test_manifest_is_written_even_when_the_run_fails(
    tmp_path: Path,
    kwargs: dict[str, Any],
) -> None:
    """Invariant I7: the audit record exists on every exit path."""
    manifest, _ = await run_pipeline(tmp_path, **kwargs)
    payload = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == manifest.status
    assert payload["error"] is not None
    assert payload["run_id"] == "20260917T130000Z-diffusion"


# --- resume ------------------------------------------------------------------


async def test_resume_reuses_checkpoints_without_calling_the_model(tmp_path: Path) -> None:
    first, _ = await run_pipeline(tmp_path)
    assert first.status == "ok"

    # A model that refuses every call: the run can only succeed from checkpoints.
    silent = ScriptedLLM()
    silent.allowed_calls = set()
    second, _ = await run_pipeline(
        tmp_path,
        llm=silent,
        request=make_request(tmp_path, resume=True),
    )

    assert second.status == "ok"
    assert silent.calls == []


async def test_resume_records_the_skipped_stages(tmp_path: Path) -> None:
    await run_pipeline(tmp_path)
    second, _ = await run_pipeline(tmp_path, request=make_request(tmp_path, resume=True))
    assert second.resume.enabled is True
    assert set(second.resume.skipped_stages) == set(STAGES)


async def test_resume_without_checkpoints_runs_normally(tmp_path: Path) -> None:
    manifest, llm = await run_pipeline(tmp_path, request=make_request(tmp_path, resume=True))
    assert manifest.status == "ok"
    assert llm.calls, "a fresh run must still call the model"
    assert manifest.resume.skipped_stages == []


async def test_a_changed_topic_invalidates_the_checkpoints(tmp_path: Path) -> None:
    await run_pipeline(tmp_path)
    changed = make_request(tmp_path, resume=True, topic="graph neural networks for climate")
    manifest, _ = await run_pipeline(tmp_path, request=changed)
    assert manifest.status == "ok"
    assert "01_search_plan" not in manifest.resume.skipped_stages


# --- cli ---------------------------------------------------------------------


def test_cli_rejects_an_empty_topic(tmp_path: Path) -> None:
    assert main(["run", "--topic", "", "--out", str(tmp_path / "out")]) == EXIT_USAGE


def test_cli_rejects_a_missing_topic(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run", "--out", str(tmp_path / "out")])
    assert excinfo.value.code == 2


def test_cli_usage_error_explains_the_field(tmp_path: Path, capsys: Any) -> None:
    main(["run", "--topic", "ab", "--out", str(tmp_path / "out")])
    assert "topic" in capsys.readouterr().err


def test_run_id_is_a_timestamp_and_a_slug() -> None:
    assert make_run_id("Diffusion Models for Weather!", now=FROZEN_NOW) == (
        "20260917T130000Z-diffusion-models-for-weather"
    )


def test_run_id_survives_a_topic_with_no_ascii() -> None:
    assert make_run_id("扩散模型用于天气预报", now=FROZEN_NOW) == "20260917T130000Z"
