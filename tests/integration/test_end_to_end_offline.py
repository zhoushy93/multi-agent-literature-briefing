"""Fully offline end-to-end runs of the real CLI: invariants I1-I9.

The run replays a recorded arXiv response and a recorded set of model replies,
so it touches no network at all and still exercises every stage from the
planner to the PDF renderer.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from briefing.cli import main
from briefing.evidence import build_haystack, quote_is_supported
from briefing.manifest import stage_path
from briefing.schemas import (
    Briefing,
    CandidateList,
    DedupedCandidates,
    Paper,
    PaperAnalysis,
    SearchPlan,
    SelectedPapers,
    VerificationReport,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"
RECORDING = "recorded_arxiv_response.xml"
TOPIC = "diffusion models for weather forecasting"
EXPECTED_PAPER_IDS = ["arxiv:2501.00042", "arxiv:2401.01234", "arxiv:2311.05555"]


def run_cli(
    out_dir: Path,
    *,
    fixtures: Path = FIXTURES,
    topic: str = TOPIC,
    env: dict[str, str] | None = None,
) -> int:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("BRIEFING_LLM_MODE", "stub")
        patch.setenv("BRIEFING_FIXTURES_DIR", str(fixtures))
        for name, value in (env or {}).items():
            patch.setenv(name, value)
        return main(["run", "--topic", topic, "--out", str(out_dir), "--no-cache"])


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One recorded run, shared by the invariant tests."""
    out_dir = tmp_path_factory.mktemp("e2e") / "run"
    assert run_cli(out_dir) == 0
    return out_dir


@pytest.fixture
def payloads(run_dir: Path) -> dict[str, Any]:
    return {
        name: json.loads((run_dir / name).read_text(encoding="utf-8"))
        for name in ("briefing.json", "papers.json", "manifest.json")
    }


def checkpoint(out_dir: Path, stage: str) -> Any:
    return json.loads(stage_path(out_dir, stage).read_text(encoding="utf-8"))["payload"]


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def compact(text: str) -> str:
    """Drop whitespace: PDF extraction breaks long URLs across lines."""
    return re.sub(r"\s+", "", text)


# --- the run itself ----------------------------------------------------------


def test_the_offline_cli_run_succeeds(run_dir: Path) -> None:
    assert run_dir.is_dir()
    for name in ("report.pdf", "briefing.md", "briefing.json", "papers.json", "manifest.json"):
        assert (run_dir / name).is_file(), name


def test_the_run_used_exactly_the_recorded_calls(payloads: dict[str, Any]) -> None:
    manifest = payloads["manifest.json"]
    assert manifest["status"] == "ok"
    # planner 1 + screener 1 + analyzer 3 + synthesizer 1
    assert manifest["llm_usage"]["calls"] == 6
    assert manifest["llm_usage"]["per_stage"] == {"S1": 1, "S4": 1, "S5": 3, "S6": 1}
    assert manifest["retrieval"]["selected"] == 3
    assert manifest["verification"]["revisions"] == 0


# --- invariants (docs/architecture.md §7) -----------------------------------


def test_invariant_i1_between_three_and_five_papers(payloads: dict[str, Any]) -> None:
    assert 3 <= len(payloads["papers.json"]["papers"]) <= 5


def test_invariant_i2_ids_are_unique_and_urls_come_from_the_recording(
    payloads: dict[str, Any],
) -> None:
    records = payloads["papers.json"]["papers"]
    ids = [record["screened"]["paper"]["paper_id"] for record in records]
    assert len(ids) == len(set(ids))
    for record in records:
        paper = record["screened"]["paper"]
        assert paper["url"].startswith("http")
        assert paper["arxiv_id"] in paper["url"] or paper["url"].endswith(paper["arxiv_id"])


def test_invariant_i3_every_citation_maps_to_one_paper(payloads: dict[str, Any]) -> None:
    briefing = payloads["briefing.json"]
    keys = [card["citation_key"] for card in briefing["paper_cards"]]
    assert keys == ["P1", "P2", "P3"]
    assert len(set(keys)) == len(keys)


def test_invariant_i4_references_come_from_retrieved_metadata(
    run_dir: Path,
    payloads: dict[str, Any],
) -> None:
    references = compact(pdf_text(run_dir / "report.pdf"))
    urls = [record["screened"]["paper"]["url"] for record in payloads["papers.json"]["papers"]]
    for url in urls:
        assert compact(url) in references, f"{url} is missing from the reference list"
    assert "https://doi.org/10.1234/jwm.2024.345" in references


def test_invariant_i5_every_paper_has_a_complete_analysis(
    payloads: dict[str, Any],
) -> None:
    records = payloads["papers.json"]["papers"]
    assert len(records) == 3
    for record in records:
        analysis = record["analysis"]
        assert analysis["paper_id"] == record["screened"]["paper"]["paper_id"]
        assert analysis["key_findings"] and analysis["limitations"]
        assert analysis["evidence"], "an analysis without evidence is not traceable"


def test_invariant_i6_checkpoints_round_trip_through_their_schemas(run_dir: Path) -> None:
    """The observable form of 'agents exchange models, never bare dicts'."""
    SearchPlan.model_validate(checkpoint(run_dir, "01_search_plan"))
    CandidateList.model_validate(checkpoint(run_dir, "02_candidates"))
    DedupedCandidates.model_validate(checkpoint(run_dir, "03_deduped"))
    SelectedPapers.model_validate(checkpoint(run_dir, "04_selected"))
    for item in checkpoint(run_dir, "05_analyses"):
        PaperAnalysis.model_validate(item)
    Briefing.model_validate(checkpoint(run_dir, "06_briefing"))
    VerificationReport.model_validate(checkpoint(run_dir, "07_verification"))


def test_invariant_i7_manifest_exists_on_failure_too(tmp_path: Path) -> None:
    """I7: the audit record is written even when the run cannot finish."""
    fixtures = copy_fixtures(tmp_path)
    recording = fixtures / RECORDING
    recording.write_text(
        keep_only_entries(recording.read_text(encoding="utf-8"), 2),
        encoding="utf-8",
    )
    out_dir = tmp_path / "failed"
    assert run_cli(out_dir, fixtures=fixtures) == 1

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "insufficient_papers"
    assert manifest["error"] is not None
    assert manifest["finished_at"] >= manifest["started_at"]


def test_invariant_i8_the_run_is_rebuildable_from_the_manifest(
    payloads: dict[str, Any],
) -> None:
    manifest = payloads["manifest.json"]
    assert manifest["model"]["id"]
    assert manifest["model"]["params"]["temperature"]["analyze"] == 0.2
    assert set(manifest["prompt_versions"].values()) == {
        "planner_v1",
        "screener_v1",
        "analyzer_v2",
        "synthesizer_v2",
        "verifier_v1",
    }
    assert manifest["retrieval"]["queries"], "the queries must be reproducible"


def test_invariant_i9_no_quote_came_from_outside_the_recording(
    run_dir: Path,
    payloads: dict[str, Any],
) -> None:
    """Re-prove every quote against the recorded text, independently of L1."""
    papers = {
        record["screened"]["paper"]["paper_id"]: record["screened"]["paper"]
        for record in payloads["papers.json"]["papers"]
    }
    for card in payloads["briefing.json"]["paper_cards"]:
        analysis = card["analysis"]
        haystack = build_haystack(Paper.model_validate(papers[analysis["paper_id"]]))
        for evidence in analysis["evidence"]:
            assert quote_is_supported(evidence["quote"], haystack), evidence["quote"]


def test_the_pdf_reports_the_recorded_papers(run_dir: Path) -> None:
    text = pdf_text(run_dir / "report.pdf")
    assert "Latent Diffusion for Seasonal Climate Emulation" in text
    assert "Diffusion Models for Medium-Range Weather Forecasting" in text
    assert "Score-Based" in text
    assert "[1]" in text and "[3]" in text


def test_the_markdown_matches_the_json(run_dir: Path, payloads: dict[str, Any]) -> None:
    markdown = (run_dir / "briefing.md").read_text(encoding="utf-8")
    assert payloads["briefing.json"]["executive_summary"] in markdown
    assert "10.1234/jwm.2024.345" in markdown, "the DOI belongs in the references"


# --- injected failure cases --------------------------------------------------


def copy_fixtures(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, target)
    return target


def rewrite_payload(path: Path, mutate: Any) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data["payload"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def keep_only_entries(xml: str, count: int) -> str:
    """Trim the recording to ``count`` entries, keeping valid XML."""
    blocks = re.findall(r"  <entry>.*?</entry>\n", xml, re.DOTALL)
    for block in blocks[count:]:
        xml = xml.replace(block, "")
    return xml


def test_failure_status_insufficient_papers(tmp_path: Path) -> None:
    fixtures = copy_fixtures(tmp_path)
    recording = fixtures / RECORDING
    recording.write_text(
        keep_only_entries(recording.read_text(encoding="utf-8"), 2),
        encoding="utf-8",
    )
    out_dir = tmp_path / "run"
    assert run_cli(out_dir, fixtures=fixtures) == 1

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "insufficient_papers"
    assert "widen the topic" in manifest["error"]["message"]
    # Two entries, returned for each of the two queries, then deduplicated.
    assert manifest["retrieval"]["candidates_found"] == 4
    assert manifest["retrieval"]["after_dedup"] == 2
    assert manifest["retrieval"]["selected"] == 0


def test_failure_status_analysis_failed(tmp_path: Path) -> None:
    fixtures = copy_fixtures(tmp_path)
    for path in sorted(fixtures.glob("analyzer_v2__*.json")):
        rewrite_payload(
            path,
            lambda payload: payload["evidence"][0].update(
                {"quote": "A sentence that appears in no paper."}
            ),
        )
    out_dir = tmp_path / "run"
    assert run_cli(out_dir, fixtures=fixtures) == 1

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "analysis_failed"
    assert "could not be analysed" in manifest["error"]["message"]


def test_failure_status_verification_failed(tmp_path: Path) -> None:
    fixtures = copy_fixtures(tmp_path)
    synthesis = fixtures / "synthesizer_v2__001.json"
    rewrite_payload(
        synthesis,
        lambda payload: payload.update({"comparison": payload["comparison"][:1]}),
    )
    # The revision asks again, and the recording answers the same way.
    shutil.copyfile(synthesis, fixtures / "synthesizer_v2__002.json")

    out_dir = tmp_path / "run"
    assert run_cli(out_dir, fixtures=fixtures) == 1

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "verification_failed"
    assert manifest["verification"]["revisions"] == 1
    assert "survived the revision" in manifest["error"]["message"]
    assert (out_dir / "_stages" / "06_briefing.json").is_file()
