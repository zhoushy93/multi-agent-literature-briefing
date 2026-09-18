"""Figure tests: deterministic SVG, wrapping, and escaping."""

from __future__ import annotations

from briefing.report.figures import (
    OverviewEntry,
    render_topic_overview,
    truncate,
    wrap_text,
)


def entry(number: int, title: str, **overrides: object) -> OverviewEntry:
    data: dict[str, object] = {
        "number": number,
        "title": title,
        "year": 2026,
        "method_family": "Latent diffusion",
    }
    data.update(overrides)
    return OverviewEntry(**data)  # type: ignore[arg-type]


# --- text helpers ------------------------------------------------------------


def test_wrap_text_keeps_every_word() -> None:
    lines = wrap_text("Latent diffusion for seasonal climate emulation", columns=40)
    assert " ".join(lines) == "Latent diffusion for seasonal climate emulation"


def test_wrap_text_marks_truncation() -> None:
    lines = wrap_text("a" * 20 + " " + "b" * 20, columns=10, max_lines=1)
    assert lines == ["aaaaaaaaaa…"], "one line of ten columns, then an ellipsis"


def test_wrap_text_counts_cjk_as_two_columns() -> None:
    # Eight columns fit four Han characters, not eight.
    assert wrap_text("扩散模型用于天气预报", columns=8, max_lines=1) == ["扩散模型…"]


def test_a_long_cjk_title_wraps_without_spaces() -> None:
    lines = wrap_text("扩散模型用于天气预报", columns=8, max_lines=3)
    assert lines == ["扩散模型", "用于天气", "预报"]
    assert "".join(lines) == "扩散模型用于天气预报"


def test_a_word_wider_than_the_line_is_still_broken() -> None:
    lines = wrap_text("antidisestablishmentarianism", columns=10, max_lines=2)
    assert len(lines) == 2
    assert all(len(line) <= 11 for line in lines)


def test_truncate_leaves_short_text_alone() -> None:
    assert truncate("short", 20) == "short"


def test_truncate_cuts_on_a_word_boundary() -> None:
    assert truncate("one two three four five", 15) == "one two three…"


def test_truncate_collapses_whitespace() -> None:
    assert truncate("  a\n b ", 20) == "a b"


# --- the figure --------------------------------------------------------------


def test_overview_draws_the_topic_and_every_paper() -> None:
    svg = render_topic_overview(
        "diffusion models for weather forecasting",
        [entry(1, "WIND"), entry(2, "PuYun-LDM"), entry(3, "LaDCast")],
    )
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "diffusion models for weather" in svg
    for title in ("WIND", "PuYun-LDM", "LaDCast"):
        assert title in svg
    assert svg.count('class="ov-card"') == 3
    assert svg.count('class="ov-badge"') == 3


def test_overview_escapes_model_output() -> None:
    """Paper titles come from the source; they must not inject markup."""
    svg = render_topic_overview(
        "topic <script>alert(1)</script>",
        [entry(1, "Title & <b>bold</b>")],
    )
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "Title &amp;" in svg


def test_overview_without_papers_is_empty() -> None:
    assert render_topic_overview("topic", []) == ""


def test_overview_is_deterministic() -> None:
    entries = [entry(1, "One"), entry(2, "Two")]
    assert render_topic_overview("topic", entries) == render_topic_overview("topic", entries)


def test_overview_handles_a_missing_year() -> None:
    svg = render_topic_overview("topic", [entry(1, "One", year=None)])
    assert "None" not in svg
    assert "Latent diffusion" in svg
