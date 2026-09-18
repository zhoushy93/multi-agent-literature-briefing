"""PDF rendering tests: real WeasyPrint output, offline."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from briefing.errors import RenderError
from briefing.report.markdown import render_markdown
from briefing.report.render_pdf import (
    RenderResult,
    ReportContext,
    WeasyPrintEngine,
    render_html,
    render_report,
    verify_pdf,
)
from briefing.schemas import (
    Briefing,
    Claim,
    ComparisonRow,
    Paper,
    PaperAnalysis,
    PaperCard,
    Query,
    ScreenedPaper,
    SearchPlan,
    SelectedPapers,
)

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
GENERATED_AT = datetime(2026, 9, 17, 13, 30, tzinfo=UTC)


def make_paper(index: int, *, lang: str) -> Paper:
    abstract = (
        f"我们研究用于天气预报的扩散模型 {index}，并在 ERA5 上评估。"
        if lang == "zh"
        else f"We study diffusion model {index} on ERA5 reanalysis."
    )
    title = f"用于天气预报的扩散模型 {index}" if lang == "zh" else f"Diffusion Study {index}"
    return Paper(
        paper_id=f"arxiv:2401.0000{index}",
        title=title,
        authors=[f"作者 {index}" if lang == "zh" else f"Author {index}"],
        year=2024,
        venue="Journal of Weather ML",
        origin="arxiv",
        url=f"https://arxiv.org/abs/2401.0000{index}",
        arxiv_id=f"2401.0000{index}",
        abstract=abstract,
        retrieved_at=RETRIEVED_AT,
    )


def make_analysis(index: int, *, lang: str) -> PaperAnalysis:
    zh = lang == "zh"
    return PaperAnalysis(
        paper_id=f"arxiv:2401.0000{index}",
        problem="长期预报精度不足。" if zh else "Long-horizon accuracy is poor.",
        method="条件扩散模型。" if zh else "A conditional diffusion model.",
        data_and_experiments="ERA5 再分析数据。" if zh else "ERA5 reanalysis.",
        key_findings=[f"第 {index} 条结论。" if zh else f"Finding {index}."],
        limitations=[f"第 {index} 条局限。" if zh else f"Limitation {index}."],
        relevance=(f"与主题的关系：第 {index} 篇。" if zh else f"Why paper {index} matters."),
        reusable_ideas=[f"第 {index} 个可复用点。" if zh else f"Idea {index}."],
        evidence=[
            {
                "field": "abstract",
                "locator": "abstract[0:20]",
                "quote": (
                    f"我们研究用于天气预报的扩散模型 {index}"
                    if zh
                    else f"We study diffusion model {index}"
                ),
            }
        ],
        confidence=0.7,
    )


def make_selected(*, lang: str = "zh", count: int = 3) -> SelectedPapers:
    return SelectedPapers(
        items=[
            ScreenedPaper(
                paper=make_paper(index, lang=lang),
                rank=index,
                relevance_score=0.9,
                rationale="relevant",
            )
            for index in range(1, count + 1)
        ]
    )


def make_plan(*, lang: str = "zh") -> SearchPlan:
    return SearchPlan(
        normalized_topic="diffusion models for weather forecasting",
        queries=[
            Query(source="arxiv", q='all:"diffusion model" AND all:forecasting', rationale="x"),
            Query(source="arxiv", q="abs:score-based", rationale="y"),
        ],
        keywords=["diffusion", "forecasting", "generative"],
        inclusion_criteria=[
            "提出了生成式预报方法" if lang == "zh" else "proposes a generative model"
        ],
        exclusion_criteria=["不是同行评议" if lang == "zh" else "is not peer reviewed"],
        time_window=(None, None),
    )


def make_briefing(*, lang: str = "zh") -> Briefing:
    zh = lang == "zh"
    return Briefing(
        run_id="20260917T1330Z-diffusion",
        topic="扩散模型用于天气预报" if zh else "diffusion models for weather forecasting",
        lang=lang,  # type: ignore[arg-type]
        executive_summary=(
            "扩散模型在中长期天气预报上稳定优于确定性基线。" if zh else "Diffusion models win."
        ),
        background_md=(
            "## 背景\n\n扩散模型已被引入气象领域 [P1]。\n\n- 采样质量更好\n- 训练更稳定"
            if zh
            else "## Background\n\nDiffusion models arrived in weather [P1]."
        ),
        method_md=(
            "## 方法\n\n本简报基于 arXiv 检索，纳入标准见下 [P2]。"
            if zh
            else "## Method\n\nWe searched arXiv [P2]."
        ),
        paper_cards=[
            PaperCard(citation_key=f"P{index}", analysis=make_analysis(index, lang=lang))
            for index in (1, 2, 3)
        ],
        comparison=[
            ComparisonRow(
                citation_key=f"P{index}",
                task="预报" if zh else "Forecasting",
                method_family="扩散" if zh else "Diffusion",
                data="ERA5",
                metrics="RMSE",
                main_result="误差更低" if zh else "Lower error",
            )
            for index in (1, 2, 3)
        ],
        gaps_and_open_questions=[
            Claim(
                text="跨区域迁移尚未验证。" if zh else "Regional transfer is untested.",
                citation_keys=["P1", "P3"],
            )
        ],
        further_reading=[
            Claim(text="建议先读 [P2]。" if zh else "Start with P2.", citation_keys=["P2"])
        ],
    )


def make_context(*, lang: str = "zh") -> ReportContext:
    return ReportContext(
        briefing=make_briefing(lang=lang),
        selected=make_selected(lang=lang),
        search_plan=make_plan(lang=lang),
        model_id="test-model",
        prompt_versions={"planner": "planner_v1", "synthesizer": "synthesizer_v2"},
        llm_calls=9,
        total_tokens=4321,
        generated_at=GENERATED_AT,
    )


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> RenderResult:
    out_dir = tmp_path_factory.mktemp("report")
    return render_report(make_context(lang="zh"), out_dir)


# --- the produced document ---------------------------------------------------


def test_report_renders_a_pdf(rendered: RenderResult) -> None:
    assert rendered.pdf_path.is_file()
    assert rendered.pdf_path.stat().st_size > 0


def test_page_count_is_positive(rendered: RenderResult) -> None:
    assert rendered.page_count > 0
    check = verify_pdf(rendered.pdf_path, expect_cjk=True)
    assert check.page_count == rendered.page_count


def test_text_is_extractable(rendered: RenderResult) -> None:
    check = verify_pdf(rendered.pdf_path, expect_cjk=True)
    assert check.text_extractable is True
    assert check.problem == ""


def test_cjk_text_is_extractable_and_fonts_embedded(rendered: RenderResult) -> None:
    check = verify_pdf(rendered.pdf_path, expect_cjk=True)
    assert check.cjk_present is True
    assert check.fonts_embedded is True
    assert check.ok is True


def test_every_chapter_is_present(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    for heading in (
        "目录",
        "一句话结论",
        "主题速览",
        "速览表",
        "主题背景",
        "检索方法与纳入排除标准",
        "逐篇论文",
        "横向对比",
        "研究空白与开放问题",
        "后续阅读建议",
        "参考文献",
        "附录：生成参数",
    ):
        assert heading in text, f"missing chapter: {heading}"


def test_every_card_shows_relevance(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    assert text.count("与主题的关系") >= 3, "one relevance row per paper card"


def test_the_overview_chapter_has_the_figure(rendered: RenderResult) -> None:
    html = rendered.html_path.read_text(encoding="utf-8")
    assert '<svg class="overview"' in html
    assert html.count('class="ov-card"') == 3


def test_table_of_contents_carries_page_numbers(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    assert re.search(r"一句话结论\s+\d+", text), "the TOC entry has no page number"
    assert re.search(r"参考文献\s+\d+", text)


def test_references_are_numbered_and_ordered(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    first = text.index("用于天气预报的扩散模型 1. Journal of Weather ML")
    second = text.index("用于天气预报的扩散模型 2. Journal of Weather ML")
    assert first < second


def test_inline_keys_become_numbers(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    assert "[P1]" not in text, "citation keys must be resolved before rendering"
    assert "扩散模型已被引入气象领域 [1]" in text.replace("\n", "")


def test_search_queries_and_criteria_are_printed(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    assert "arxiv:" in text
    assert "纳入" in text


def test_appendix_records_the_generation_parameters(rendered: RenderResult) -> None:
    text = pdf_text(rendered.pdf_path)
    assert "test-model" in text
    assert "synthesizer_v2" in text
    assert "2026-09-17 13:30 UTC" in text


def test_html_is_kept_as_a_stage_artifact(rendered: RenderResult) -> None:
    assert rendered.html_path.name == "08_report.html"
    assert rendered.html_path.parent.name == "_stages"
    assert "<!DOCTYPE html>" in rendered.html_path.read_text(encoding="utf-8")


def test_renderer_is_recorded(rendered: RenderResult) -> None:
    assert rendered.renderer == "weasyprint"
    assert rendered.fallback is False


def test_english_briefing_renders_without_cjk(rendered: RenderResult) -> None:
    html = render_html(make_context(lang="en"))
    assert "Executive summary" in html
    assert "Contents" in html


# --- verification gates -------------------------------------------------------


def test_a_pdf_without_chinese_text_fails_the_cjk_gate(tmp_path: Path) -> None:
    result = render_report(make_context(lang="en"), tmp_path / "en")
    check = verify_pdf(result.pdf_path, expect_cjk=True)
    assert check.ok is False
    assert "Chinese text" in check.problem


def test_missing_pdf_is_rejected(tmp_path: Path) -> None:
    check = verify_pdf(tmp_path / "nope.pdf", expect_cjk=False)
    assert check.ok is False
    assert check.page_count == 0


# --- engine fallback ----------------------------------------------------------


class BrokenEngine:
    name = "broken"

    def render(self, html: str, target: Path, *, html_path: Path) -> None:
        raise RenderError("engine exploded")


class StubEngine:
    """Stands in for Chromium: writes a genuine PDF produced elsewhere."""

    name = "stub-fallback"

    def __init__(self, source: Path) -> None:
        self._source = source

    def render(self, html: str, target: Path, *, html_path: Path) -> None:
        shutil.copyfile(self._source, target)


def test_fallback_engine_is_used_and_recorded(tmp_path: Path) -> None:
    baseline = render_report(
        make_context(lang="zh"),
        tmp_path / "baseline",
        engines=[WeasyPrintEngine()],
    )
    result = render_report(
        make_context(lang="zh"),
        tmp_path / "fallback",
        engines=[BrokenEngine(), StubEngine(baseline.pdf_path)],
    )
    assert result.renderer == "stub-fallback"
    assert result.fallback is True


def test_all_engines_failing_is_a_render_error(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="no pdf engine"):
        render_report(make_context(), tmp_path / "broken", engines=[BrokenEngine()])


def test_an_engine_that_produces_rubbish_is_rejected(tmp_path: Path) -> None:
    class RubbishEngine:
        name = "rubbish"

        def render(self, html: str, target: Path, *, html_path: Path) -> None:
            target.write_text("this is not a pdf", encoding="utf-8")

    with pytest.raises(RenderError, match="rubbish"):
        render_report(make_context(), tmp_path / "rubbish", engines=[RubbishEngine()])


# --- markdown subset ----------------------------------------------------------


def test_markdown_headings_become_html() -> None:
    assert render_markdown("## Title") == "<h3>Title</h3>"
    assert render_markdown("# Top") == "<h2>Top</h2>"


def test_markdown_bullets_become_a_list() -> None:
    assert render_markdown("- one\n- two") == "<ul>\n<li>one</li>\n<li>two</li>\n</ul>"


def test_markdown_emphasis_is_rendered() -> None:
    assert render_markdown("a **bold** and *soft* word") == (
        "<p>a <strong>bold</strong> and <em>soft</em> word</p>"
    )


def test_markdown_escapes_model_output() -> None:
    """Prose from a model must never inject markup."""
    assert render_markdown("<script>alert(1)</script>") == (
        "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"
    )


def test_markdown_joins_wrapped_paragraph_lines() -> None:
    assert render_markdown("one\ntwo") == "<p>one two</p>"


@pytest.mark.parametrize("markdown", ["", "   ", "\n\n"])
def test_empty_markdown_is_empty_html(markdown: str) -> None:
    assert render_markdown(markdown) == ""
