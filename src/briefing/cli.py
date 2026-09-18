"""Command line entry point for the briefing pipeline.

Exit codes follow AGENTS.md §9: 2 for a usage problem, 1 for a run that did not
produce a briefing, 0 for success. Logs are structured JSON; user-facing
summaries go to stdout and stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from briefing import __version__
from briefing.config import Settings
from briefing.errors import BriefingError
from briefing.llm import create_llm_client
from briefing.llm.budget import BudgetGuard
from briefing.manifest import RunManifest
from briefing.orchestrator import DEFAULT_CACHE_DIR, Orchestrator
from briefing.schemas import TopicRequest
from briefing.sources import create_sources
from briefing.sources.cache import CacheStore

PROG = "briefing"

# Exit codes per AGENTS.md §9.
EXIT_OK = 0
EXIT_RUN_ERROR = 1
EXIT_USAGE = 2

_LOG_FIELDS = (
    "stage",
    "engine",
    "reason",
    "detail",
    "location",
    "source",
    "query",
    "paper_id",
    "findings",
    "calls",
    "abstract_chars_kept",
)


class JsonFormatter(logging.Formatter):
    """AGENTS.md §9: one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not verbose:
        # WeasyPrint narrates every layout pass at INFO; keep the CLI readable.
        logging.getLogger("weasyprint").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Retrieve papers for a topic, analyze them, and emit a PDF briefing.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the full briefing pipeline.")
    run_parser.add_argument("--topic", required=True, help="Research topic to brief.")
    run_parser.add_argument("--out", required=True, help="Output directory for run artifacts.")
    run_parser.add_argument(
        "--lang",
        choices=["zh", "en"],
        default=None,
        help="Report language. Defaults to the language of --topic.",
    )
    run_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the retrieval cache.",
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse stage checkpoints whose input fingerprint still matches.",
    )
    run_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Emit debug-level structured logs.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    if args.command == "run":
        return run_command(args)

    return EXIT_USAGE


def make_run_id(topic: str, *, now: datetime | None = None) -> str:
    """``<UTC timestamp>-<topic slug>``, stable enough to sort run directories."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
    return f"{stamp}-{slug}" if slug else stamp


def build_request(args: argparse.Namespace) -> TopicRequest:
    return TopicRequest(
        topic=args.topic,
        lang=args.lang,
        out_dir=Path(args.out),
        no_cache=args.no_cache,
        resume=args.resume,
        run_id=make_run_id(args.topic),
    )


def run_command(args: argparse.Namespace) -> int:
    configure_logging(bool(args.verbose))
    try:
        request = build_request(args)
    except ValidationError as exc:
        sys.stderr.write("invalid arguments:\n")
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "request"
            sys.stderr.write(f"  {location}: {error['msg']}\n")
        return EXIT_USAGE

    settings = Settings()
    try:
        manifest = asyncio.run(execute(request, settings))
    except BriefingError as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return EXIT_RUN_ERROR

    if manifest.status == "ok":
        sys.stdout.write(
            f"ok {manifest.run_id}: {manifest.retrieval.selected} papers, "
            f"{manifest.llm_usage.calls} model calls, {manifest.duration_s}s\n"
        )
        for name, path in sorted(manifest.artifacts.items()):
            sys.stdout.write(f"  {name}: {request.out_dir / path}\n")
        return EXIT_OK

    detail = manifest.error.message if manifest.error else "no detail"
    sys.stderr.write(f"{manifest.status}: {detail}\n")
    sys.stderr.write(f"  manifest: {request.out_dir / 'manifest.json'}\n")
    return EXIT_RUN_ERROR


async def execute(request: TopicRequest, settings: Settings) -> RunManifest:
    """Build the run's collaborators, execute it, and always close the clients."""
    budget = BudgetGuard(
        max_calls=settings.max_llm_calls,
        max_tokens=settings.max_tokens_budget,
    )
    llm = create_llm_client(settings, budget)
    cache = CacheStore(root=DEFAULT_CACHE_DIR, enabled=not request.no_cache)
    sources = create_sources(settings, cache)
    try:
        return await Orchestrator(
            request=request,
            settings=settings,
            llm=llm,
            sources=sources,
            cache=cache,
            budget=budget,
        ).run()
    finally:
        for source in sources:
            close_source = getattr(source, "aclose", None)
            if callable(close_source):
                await close_source()
        close = getattr(llm, "aclose", None)
        if callable(close):
            await close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
