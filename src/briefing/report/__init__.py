"""Report rendering: deterministic numbering, HTML, and PDF output."""

from __future__ import annotations

from briefing.report.markdown import render_markdown
from briefing.report.references import (
    Reference,
    build_reference_map,
    build_references,
    format_reference,
    resolve_citations,
)
from briefing.report.render_pdf import (
    ChromiumEngine,
    PdfCheck,
    PdfEngine,
    RenderResult,
    ReportContext,
    WeasyPrintEngine,
    chromium_available,
    render_html,
    render_report,
    verify_pdf,
)

__all__ = [
    "ChromiumEngine",
    "PdfCheck",
    "PdfEngine",
    "Reference",
    "RenderResult",
    "ReportContext",
    "WeasyPrintEngine",
    "build_reference_map",
    "build_references",
    "chromium_available",
    "format_reference",
    "render_html",
    "render_markdown",
    "render_report",
    "resolve_citations",
    "verify_pdf",
]
