"""Verification layers: deterministic (always on) and semantic (optional)."""

from __future__ import annotations

from briefing.verify.deterministic import verify_deterministic
from briefing.verify.report import (
    Outcome,
    build_report,
    fatal_findings,
    has_fatal,
    resolve_outcome,
)
from briefing.verify.semantic import (
    ClaimRef,
    SemanticVerifier,
    collect_claims,
    create_semantic_verifier,
)

__all__ = [
    "ClaimRef",
    "Outcome",
    "SemanticVerifier",
    "build_report",
    "collect_claims",
    "create_semantic_verifier",
    "fatal_findings",
    "has_fatal",
    "resolve_outcome",
    "verify_deterministic",
]
