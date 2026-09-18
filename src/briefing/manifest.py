"""Run manifest and stage checkpoints (docs/architecture.md §2.3, §8).

The manifest answers "which model, which prompt versions, which parameters
produced this report" for every run, including the ones that failed. The
orchestrator writes it from a ``finally`` block, so a crash cannot leave a run
directory without one (invariant I7).

Checkpoints make ``--resume`` safe: each stage stores the fingerprint of its
inputs, and a checkpoint is only reused when that fingerprint still matches.
Because every fingerprint is built from the previous stage's payload, one
changed input invalidates that stage and everything downstream of it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from briefing.schemas import Contract, PaperAnalysis, ScreenedPaper

Status = Literal[
    "ok",
    "run_error",
    "insufficient_papers",
    "analysis_failed",
    "verification_failed",
    "budget_exceeded",
    "render_failed",
]

STAGES: tuple[str, ...] = (
    "01_search_plan",
    "02_candidates",
    "03_deduped",
    "04_selected",
    "05_analyses",
    "06_briefing",
    "07_verification",
)

STAGES_DIR = "_stages"


# --- models -------------------------------------------------------------------


class ModelParams(Contract):
    temperature: dict[str, float] = Field(default_factory=dict)
    top_p: float = 1.0
    max_tokens: int = 4096
    thinking: str = "auto"


class ModelInfo(Contract):
    id: str
    reasoning_id: str | None = None
    base_url_host: str = ""
    params: ModelParams = Field(default_factory=ModelParams)


class CacheInfo(Contract):
    hits: int = 0
    misses: int = 0
    bypassed: bool = False
    ttl_days: int = 0


class RetrievalInfo(Contract):
    queries: list[str] = Field(default_factory=list)
    candidates_found: int = 0
    after_dedup: int = 0
    selected: int = 0
    per_source_counts: dict[str, int] = Field(default_factory=dict)
    cache: CacheInfo = Field(default_factory=CacheInfo)
    dropped: dict[str, int] = Field(default_factory=dict)


class LLMUsage(Contract):
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    est_cost_usd: float | None = None
    per_stage: dict[str, int] = Field(default_factory=dict)


class BudgetInfo(Contract):
    max_calls: int = 0
    max_tokens: int = 0
    exceeded: bool = False
    reason: str | None = None


class VerificationInfo(Contract):
    passed: bool = False
    deterministic_violations: int = 0
    semantic_violations: int = 0
    revisions: int = 0
    semantic_enabled: bool = False


class RendererInfo(Contract):
    name: str = ""
    version: str | None = None
    fallback: bool = False
    cjk_font_embedded: bool = False


class ResumeInfo(Contract):
    enabled: bool = False
    skipped_stages: list[str] = Field(default_factory=list)


class ErrorInfo(Contract):
    type: str
    message: str


class RunManifest(Contract):
    """The audit record of one run."""

    run_id: str
    topic: str
    lang: str | None = None
    status: Status
    started_at: datetime
    finished_at: datetime
    duration_s: float
    model: ModelInfo
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    retrieval: RetrievalInfo = Field(default_factory=RetrievalInfo)
    llm_usage: LLMUsage = Field(default_factory=LLMUsage)
    budget: BudgetInfo = Field(default_factory=BudgetInfo)
    verification: VerificationInfo = Field(default_factory=VerificationInfo)
    renderer: RendererInfo = Field(default_factory=RendererInfo)
    stage_timings_s: dict[str, float] = Field(default_factory=dict)
    resume: ResumeInfo = Field(default_factory=ResumeInfo)
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: ErrorInfo | None = None


class PaperRecord(Contract):
    """One entry of ``papers.json``: metadata plus its analysis."""

    screened: ScreenedPaper
    analysis: PaperAnalysis


class PapersArtifact(Contract):
    run_id: str
    papers: list[PaperRecord] = Field(default_factory=list)


# --- checkpoints --------------------------------------------------------------


def fingerprint(*parts: Any) -> str:
    """Hash a stage's inputs, so a stale checkpoint can be detected."""
    canonical = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stage_path(out_dir: Path, stage: str, suffix: str = "json") -> Path:
    return out_dir / STAGES_DIR / f"{stage}.{suffix}"


def write_checkpoint(out_dir: Path, stage: str, stage_fingerprint: str, payload: Any) -> None:
    path = stage_path(out_dir, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "stage": stage,
                "input_fingerprint": stage_fingerprint,
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def read_checkpoint(
    out_dir: Path,
    stage: str,
    expected_fingerprint: str,
) -> Any | None:
    """Return the stored payload when it is still valid for this stage."""
    path = stage_path(out_dir, stage)
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if stored.get("input_fingerprint") != expected_fingerprint:
        return None
    return stored.get("payload")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def summarise_dropped(entries: Sequence[dict[str, int]]) -> dict[str, int]:
    """Merge per-source drop ledgers into one."""
    merged: dict[str, int] = {}
    for ledger in entries:
        for key, value in ledger.items():
            merged[key] = merged.get(key, 0) + value
    return merged
