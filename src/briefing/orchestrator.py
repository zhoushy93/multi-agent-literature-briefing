"""The pipeline: S1..S8, with checkpoints, budget accounting and a manifest.

This module holds *only* control flow. Prompt text lives in ``prompts/``,
decisions about data live in the agents, and the manifest schema lives in
``manifest.py`` (docs/architecture.md §2.1).

Two promises are enforced here:

* ``manifest.json`` is written on every exit path, including failures (I7);
* a paper that cannot be analysed fails the run instead of quietly shrinking
  the briefing (I5).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from briefing.agents.analyzer import TEMPERATURE as ANALYZE_TEMPERATURE
from briefing.agents.analyzer import Analyzer, analyze_all
from briefing.agents.normalizer import normalize
from briefing.agents.planner import TEMPERATURE as PLAN_TEMPERATURE
from briefing.agents.planner import Planner
from briefing.agents.retriever import Retriever
from briefing.agents.screener import TEMPERATURE as SCREEN_TEMPERATURE
from briefing.agents.screener import Screener
from briefing.agents.synthesizer import TEMPERATURE as SYNTH_TEMPERATURE
from briefing.agents.synthesizer import Synthesizer, detect_language
from briefing.config import Settings
from briefing.errors import (
    AnalysisFailedError,
    BriefingError,
    BudgetExceeded,
    InsufficientPapersError,
    RenderError,
    VerificationFailedError,
)
from briefing.llm.base import LLMClient
from briefing.llm.budget import BudgetGuard
from briefing.manifest import (
    BudgetInfo,
    CacheInfo,
    ErrorInfo,
    LLMUsage,
    ModelInfo,
    ModelParams,
    PaperRecord,
    PapersArtifact,
    RendererInfo,
    ResumeInfo,
    RetrievalInfo,
    RunManifest,
    Status,
    VerificationInfo,
    fingerprint,
    read_checkpoint,
    write_checkpoint,
    write_json,
)
from briefing.prompts.loader import PromptLoader
from briefing.report.briefing_md import render_briefing_markdown
from briefing.report.references import build_references
from briefing.report.render_pdf import PdfEngine, RenderResult, ReportContext, render_report
from briefing.schemas import (
    Briefing,
    CandidateList,
    DedupedCandidates,
    PaperAnalysis,
    SearchPlan,
    SelectedPapers,
    TopicRequest,
    VerificationReport,
    Violation,
)
from briefing.sources.base import Source
from briefing.sources.cache import DEFAULT_TTL_DAYS, CacheStore
from briefing.verify.deterministic import verify_deterministic
from briefing.verify.report import build_report, fatal_findings
from briefing.verify.semantic import TEMPERATURE as VERIFY_TEMPERATURE
from briefing.verify.semantic import SemanticVerifier, create_semantic_verifier

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/cache")


class Orchestrator:
    """Drives one briefing run and returns its manifest."""

    def __init__(
        self,
        *,
        request: TopicRequest,
        settings: Settings,
        llm: LLMClient,
        sources: Sequence[Source],
        cache: CacheStore | None = None,
        engines: Sequence[PdfEngine] | None = None,
        prompts: PromptLoader | None = None,
        budget: BudgetGuard | None = None,
        semantic_verifier: SemanticVerifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.request = request
        self.settings = settings
        self.llm = llm
        self.sources = list(sources)
        self.cache = cache or CacheStore(
            root=DEFAULT_CACHE_DIR,
            enabled=not request.no_cache,
        )
        self.engines = engines
        self.prompts = prompts or PromptLoader()
        self.budget = budget or BudgetGuard(
            max_calls=settings.max_llm_calls,
            max_tokens=settings.max_tokens_budget,
        )
        self.semantic_verifier = semantic_verifier or create_semantic_verifier(
            settings,
            llm,
        )
        self.clock = clock or (lambda: datetime.now(UTC))
        self.out_dir = request.out_dir

        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._timings: dict[str, float] = {}
        self._skipped: list[str] = []
        self._revisions = 0
        # Filled in as stages complete, so a failure half way through still
        # reports what actually happened instead of resetting to empty values.
        self._retrieval = RetrievalInfo()
        self._report: VerificationReport | None = None
        self._renderer = RendererInfo()

    # --- entry point --------------------------------------------------------

    async def run(self) -> RunManifest:
        started = self.clock()
        started_monotonic = time.monotonic()

        status: Status = "run_error"
        error: ErrorInfo | None = None
        artifacts: dict[str, str] = {}
        lang: str | None = self.request.lang

        manifest: RunManifest | None = None
        try:
            plan = await self._plan()
            candidates = await self._retrieve(plan)
            deduped = self._normalize(candidates)
            self._retrieval = RetrievalInfo(
                queries=list(candidates.queries_used),
                candidates_found=len(candidates.papers),
                after_dedup=len(deduped.papers),
                per_source_counts=dict(candidates.per_source_counts),
                cache=CacheInfo(
                    hits=candidates.cache.hits,
                    misses=candidates.cache.misses,
                    bypassed=candidates.cache.bypassed,
                    ttl_days=DEFAULT_TTL_DAYS,
                ),
                dropped=dict(deduped.dropped),
            )
            selected = await self._screen(deduped, plan)
            self._retrieval.selected = len(selected.items)
            analyses = await self._analyze(selected)
            briefing, _report = await self._verified_briefing(selected, analyses)
            lang = briefing.lang
            rendered = self._render(briefing, selected, plan)
            self._renderer = RendererInfo(
                name=rendered.renderer,
                version=_renderer_version(rendered.renderer),
                fallback=rendered.fallback,
                cjk_font_embedded=rendered.fonts_embedded,
            )
            artifacts = self._write_artifacts(briefing, selected, analyses, plan)
            status = "ok"
        except InsufficientPapersError as exc:
            status, error = "insufficient_papers", _error_info(exc)
        except AnalysisFailedError as exc:
            status, error = "analysis_failed", _error_info(exc)
        except BudgetExceeded as exc:
            status, error = "budget_exceeded", _error_info(exc)
        except VerificationFailedError as exc:
            status, error = "verification_failed", _error_info(exc)
        except RenderError as exc:
            status, error = "render_failed", _error_info(exc)
        except BriefingError as exc:
            status, error = "run_error", _error_info(exc)
        except Exception as exc:
            status, error = "run_error", _error_info(exc)

        finally:
            manifest = self._build_manifest(
                status=status,
                error=error,
                started=started,
                duration=time.monotonic() - started_monotonic,
                artifacts=artifacts,
                lang=lang,
            )
            write_json(self.out_dir / "manifest.json", manifest.model_dump(mode="json"))

        if manifest is None:  # pragma: no cover - the finally block always assigns
            raise RuntimeError("the manifest was not built")
        return manifest

    # --- stages -------------------------------------------------------------

    async def _plan(self) -> SearchPlan:
        stage_fingerprint = fingerprint(
            "S1",
            self.request.topic,
            self.request.lang,
            self.request.time_window,
            self.settings.deepseek_model,
            self.prompts.load("planner_v1").version,
        )
        cached = self._resume("01_search_plan", stage_fingerprint)
        if cached is not None:
            return SearchPlan.model_validate(cached)

        started = time.monotonic()
        plan = await Planner(
            llm=self.llm,
            prompts=self.prompts,
            max_tokens=self.settings.max_output_tokens,
            available_sources=[source.name for source in self.sources],
        ).run(self.request)
        self._timings["S1"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "01_search_plan",
            stage_fingerprint,
            plan.model_dump(mode="json"),
        )
        return plan

    async def _retrieve(self, plan: SearchPlan) -> CandidateList:
        stage_fingerprint = fingerprint(
            "S2",
            plan.model_dump(mode="json"),
            [source.name for source in self.sources],
            self.request.no_cache,
        )
        cached = self._resume("02_candidates", stage_fingerprint)
        if cached is not None:
            return CandidateList.model_validate(cached)

        started = time.monotonic()
        candidates = await Retriever(
            sources=self.sources,
            cache=self.cache,
            max_concurrency=self.settings.max_concurrency,
        ).run(plan)
        self._timings["S2"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "02_candidates",
            stage_fingerprint,
            candidates.model_dump(mode="json"),
        )
        return candidates

    def _normalize(self, candidates: CandidateList) -> DedupedCandidates:
        stage_fingerprint = fingerprint("S3", candidates.model_dump(mode="json"))
        cached = self._resume("03_deduped", stage_fingerprint)
        if cached is not None:
            return DedupedCandidates.model_validate(cached)

        started = time.monotonic()
        deduped = normalize(candidates, max_candidates=self.settings.max_candidates)
        self._timings["S3"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "03_deduped",
            stage_fingerprint,
            deduped.model_dump(mode="json"),
        )
        return deduped

    async def _screen(
        self,
        deduped: DedupedCandidates,
        plan: SearchPlan,
    ) -> SelectedPapers:
        prompt_version = self.prompts.load("screener_v1").version
        stage_fingerprint = fingerprint(
            "S4",
            deduped.model_dump(mode="json"),
            plan.model_dump(mode="json"),
            self.request.min_papers,
            self.request.max_papers,
            prompt_version,
            self.settings.deepseek_model,
        )
        cached = self._resume("04_selected", stage_fingerprint)
        if cached is not None:
            return SelectedPapers.model_validate(cached)

        started = time.monotonic()
        selected = await Screener(
            llm=self.llm,
            prompts=self.prompts,
            min_papers=self.request.min_papers,
            max_papers=self.request.max_papers,
            max_tokens=self.settings.max_output_tokens,
        ).run(deduped, plan)
        self._timings["S4"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "04_selected",
            stage_fingerprint,
            selected.model_dump(mode="json"),
        )
        return selected

    async def _analyze(self, selected: SelectedPapers) -> list[PaperAnalysis]:
        stage_fingerprint = fingerprint(
            "S5",
            selected.model_dump(mode="json"),
            self.prompts.load("analyzer_v2").version,
            self.settings.deepseek_model,
        )
        cached = self._resume("05_analyses", stage_fingerprint)
        if cached is not None:
            return [PaperAnalysis.model_validate(item) for item in cached]

        started = time.monotonic()
        analyses = await analyze_all(
            Analyzer(
                llm=self.llm,
                prompts=self.prompts,
                topic=self.request.topic,
                lang=self.request.lang or detect_language(self.request.topic),
                max_tokens=self.settings.max_output_tokens,
            ),
            list(selected.items),
            max_concurrency=self.settings.max_concurrency,
            semaphore=self._semaphore,
        )
        self._timings["S5"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "05_analyses",
            stage_fingerprint,
            [analysis.model_dump(mode="json") for analysis in analyses],
        )
        return analyses

    async def _synthesize(
        self,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
        *,
        violations: Sequence[Violation] = (),
        revision: int = 0,
    ) -> Briefing:
        stage_fingerprint = fingerprint(
            "S6",
            selected.model_dump(mode="json"),
            [analysis.model_dump(mode="json") for analysis in analyses],
            self.request.run_id,
            self.request.topic,
            self.request.lang,
            self.prompts.load("synthesizer_v2").version,
            self.settings.deepseek_model,
            revision,
        )
        cached = self._resume("06_briefing", stage_fingerprint)
        if cached is not None:
            return Briefing.model_validate(cached)

        started = time.monotonic()
        briefing = await Synthesizer(
            llm=self.llm,
            prompts=self.prompts,
            max_tokens=self.settings.max_output_tokens,
        ).run(selected, analyses, self.request, violations=violations)
        self._timings[f"S6.revision{revision}"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "06_briefing",
            stage_fingerprint,
            briefing.model_dump(mode="json"),
        )
        return briefing

    async def _verify(
        self,
        briefing: Briefing,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
        *,
        revision_used: bool,
        revision: int,
    ) -> VerificationReport:
        stage_fingerprint = fingerprint(
            "S7",
            briefing.model_dump(mode="json"),
            selected.model_dump(mode="json"),
            self.prompts.load("verifier_v1").version,
            self.semantic_verifier is not None,
            revision,
        )
        cached = self._resume("07_verification", stage_fingerprint)
        if cached is not None:
            report = VerificationReport.model_validate(cached)
            self._report = report
            return report

        started = time.monotonic()
        expected_lang = self.request.lang or detect_language(self.request.topic)
        deterministic = verify_deterministic(
            briefing,
            selected,
            expected_lang=expected_lang,
        )
        semantic: list[Violation] = []
        if self.semantic_verifier is not None:
            semantic = await self.semantic_verifier.run(briefing, selected, analyses)
        report = build_report(deterministic, semantic, revision_used=revision_used)
        self._report = report
        self._timings[f"S7.revision{revision}"] = _seconds(started)
        write_checkpoint(
            self.out_dir,
            "07_verification",
            stage_fingerprint,
            report.model_dump(mode="json"),
        )
        return report

    async def _verified_briefing(
        self,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
    ) -> tuple[Briefing, VerificationReport]:
        briefing = await self._synthesize(selected, analyses, revision=0)
        report = await self._verify(
            briefing,
            selected,
            analyses,
            revision_used=False,
            revision=0,
        )
        if report.passed:
            return briefing, report

        # One revision, then the run either passes or ends (architecture §5).
        self._revisions = 1
        findings = fatal_findings(report)
        logger.info("revising the briefing", extra={"findings": len(findings)})
        briefing = await self._synthesize(
            selected,
            analyses,
            violations=findings,
            revision=1,
        )
        report = await self._verify(
            briefing,
            selected,
            analyses,
            revision_used=True,
            revision=1,
        )
        if not report.passed:
            remaining = fatal_findings(report)
            detail = "; ".join(f"{item.kind} at {item.location}" for item in remaining[:3])
            raise VerificationFailedError(
                f"{len(remaining)} finding(s) survived the revision: {detail}"
            )
        return briefing, report

    def _render(
        self,
        briefing: Briefing,
        selected: SelectedPapers,
        plan: SearchPlan,
    ) -> RenderResult:
        started = time.monotonic()
        usage = self.budget.snapshot()
        result = render_report(
            ReportContext(
                briefing=briefing,
                selected=selected,
                search_plan=plan,
                model_id=self.settings.deepseek_model,
                prompt_versions=self._prompt_versions(),
                llm_calls=usage.calls,
                total_tokens=usage.total_tokens,
                generated_at=self.clock(),
            ),
            self.out_dir,
            engines=self.engines,
        )
        self._timings["S8"] = _seconds(started)
        return result

    def _write_artifacts(
        self,
        briefing: Briefing,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
        plan: SearchPlan,
    ) -> dict[str, str]:
        usage = self.budget.snapshot()
        write_json(self.out_dir / "briefing.json", briefing.model_dump(mode="json"))
        (self.out_dir / "briefing.md").write_text(
            render_briefing_markdown(
                briefing,
                selected,
                build_references(selected),
                search_plan=plan,
                model_id=self.settings.deepseek_model,
                prompt_versions=self._prompt_versions(),
            ),
            encoding="utf-8",
        )
        write_json(
            self.out_dir / "papers.json",
            PapersArtifact(
                run_id=self.request.run_id,
                papers=[
                    PaperRecord(screened=screened, analysis=analysis)
                    for screened, analysis in zip(selected.items, analyses, strict=True)
                ],
            ).model_dump(mode="json"),
        )
        logger.info("artifacts written", extra={"calls": usage.calls})
        return {
            "report_pdf": "report.pdf",
            "briefing_md": "briefing.md",
            "briefing_json": "briefing.json",
            "papers_json": "papers.json",
        }

    # --- helpers ------------------------------------------------------------

    def _resume(self, stage: str, stage_fingerprint: str) -> Any | None:
        """A checkpoint payload: raw JSON, validated by the caller's model."""
        if not self.request.resume:
            return None
        payload = read_checkpoint(self.out_dir, stage, stage_fingerprint)
        if payload is not None:
            self._skipped.append(stage)
            logger.info("resumed from checkpoint", extra={"stage": stage})
        return payload

    def _prompt_versions(self) -> dict[str, str]:
        return {name: prompt.version for name, prompt in self.prompts.load_all().items()}

    def _build_manifest(
        self,
        *,
        status: Status,
        error: ErrorInfo | None,
        started: datetime,
        duration: float,
        artifacts: dict[str, str],
        lang: str | None,
    ) -> RunManifest:
        usage = self.budget.snapshot()
        report = self._report
        return RunManifest(
            run_id=self.request.run_id,
            topic=self.request.topic,
            lang=lang or self.request.lang,
            status=status,
            started_at=started,
            finished_at=self.clock(),
            duration_s=round(duration, 3),
            model=ModelInfo(
                id=self.settings.deepseek_model,
                reasoning_id=self.settings.deepseek_model_reasoning,
                base_url_host=urlparse(self.settings.deepseek_base_url).netloc,
                params=ModelParams(
                    thinking=self.settings.deepseek_thinking,
                    temperature={
                        "plan": PLAN_TEMPERATURE,
                        "screen": SCREEN_TEMPERATURE,
                        "analyze": ANALYZE_TEMPERATURE,
                        "synthesize": SYNTH_TEMPERATURE,
                        "verify": VERIFY_TEMPERATURE,
                    },
                ),
            ),
            prompt_versions=self._prompt_versions(),
            retrieval=self._retrieval,
            llm_usage=LLMUsage(
                calls=usage.calls,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                est_cost_usd=None,
                per_stage=usage.per_stage_calls,
            ),
            budget=BudgetInfo(
                max_calls=usage.max_calls,
                max_tokens=usage.max_tokens,
                exceeded=usage.exceeded_reason is not None,
                reason=usage.exceeded_reason,
            ),
            verification=VerificationInfo(
                passed=report.passed if report else False,
                deterministic_violations=len(report.deterministic) if report else 0,
                semantic_violations=len(report.semantic) if report else 0,
                revisions=self._revisions,
                semantic_enabled=self.semantic_verifier is not None,
            ),
            renderer=self._renderer,
            stage_timings_s=dict(self._timings),
            resume=ResumeInfo(enabled=self.request.resume, skipped_stages=list(self._skipped)),
            artifacts=artifacts,
            error=error,
        )


def _seconds(started_monotonic: float) -> float:
    return round(time.monotonic() - started_monotonic, 3)


def _error_info(exc: BaseException) -> ErrorInfo:
    return ErrorInfo(type=type(exc).__name__, message=str(exc))


def _renderer_version(name: str) -> str | None:
    """The engine's own version, when it can be read without importing it."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
