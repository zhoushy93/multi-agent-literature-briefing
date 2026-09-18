"""Deterministic SVG figures and small text helpers for the report.

No charting dependency: the shapes are simple, and building them in code keeps
the figure reproducible and lets tests assert exactly what is drawn. Every
piece of text is XML-escaped, so model output cannot inject markup.

WeasyPrint renders inline SVG with its own engine, which does **not** apply the
document's CSS classes to SVG elements — styling via classes alone produced
solid black shapes. Every shape therefore carries its presentation attributes
(including the CJK font chain) directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

WIDTH = 720
TOPIC_HEIGHT = 34
BOX_HEIGHT = 104
LAYOUT_TOP = 78
GAP = 12
MARGIN = 10
MAX_BOX_WIDTH = 176

_TITLE_CHARS = 15  # per line, at the title font size below
_META_CHARS = 24

FONT_FAMILY = "Noto Sans CJK SC, Source Han Sans SC, STHeiti, PingFang SC, sans-serif"
INK = "#1a1a1a"
MUTED = "#666666"
LINE = "#b9c4d0"


@dataclass(frozen=True)
class OverviewEntry:
    """One paper as drawn in the topic overview."""

    number: int
    title: str
    year: int | None
    method_family: str


# --- text helpers -------------------------------------------------------------


def _visual_width(text: str) -> int:
    """Count CJK characters as two columns so wrapping works for both scripts."""
    return sum(2 if ord(character) > 0x2E7F else 1 for character in text)


def _pieces(text: str, columns: int) -> list[tuple[str, str]]:
    """Split into ``(separator, text)`` pieces.

    Words keep a space in front of them; a word wider than a whole line (a long
    CJK title, say) is broken by character so it can still wrap and truncate.
    """
    pieces: list[tuple[str, str]] = []
    for index, word in enumerate(text.split()):
        separator = "" if index == 0 else " "
        if _visual_width(word) <= columns:
            pieces.append((separator, word))
            continue
        current = ""
        width = 0
        for character in word:
            character_width = 2 if ord(character) > 0x2E7F else 1
            if current and width + character_width > columns:
                pieces.append((separator, current))
                separator = ""
                current, width = character, character_width
            else:
                current += character
                width += character_width
        if current:
            pieces.append((separator, current))
    return pieces


def wrap_text(text: str, columns: int, max_lines: int = 2) -> list[str]:
    """Greedy wrap by visual width, truncating with an ellipsis when needed."""
    pieces = _pieces(text, columns)
    lines: list[str] = []
    current = ""
    index = 0
    while index < len(pieces):
        separator, piece = pieces[index]
        candidate = f"{current}{separator}{piece}" if current else piece
        if current and _visual_width(candidate) > columns:
            lines.append(current)
            current = ""
            if len(lines) == max_lines:
                break
            continue  # retry the same piece on the next line
        current = candidate
        index += 1

    if current and len(lines) < max_lines:
        lines.append(current)

    if not lines:
        return [""]
    if index < len(pieces):
        lines[-1] = f"{lines[-1].rstrip()}…"
    return lines[:max_lines]


def truncate(text: str, limit: int) -> str:
    """Trim a table cell's prose to ``limit`` characters, on a word boundary."""
    stripped = " ".join(text.split())
    if len(stripped) <= limit:
        return stripped
    cut = stripped[: limit - 1]
    boundary = cut.rfind(" ")
    if boundary > limit // 2:
        cut = cut[:boundary]
    return f"{cut.rstrip()}…"


# --- the figure ---------------------------------------------------------------


def _text_lines(
    lines: Sequence[str],
    *,
    x: float,
    y: float,
    line_height: float,
    css_class: str,
    size: float,
    fill: str,
    anchor: str = "start",
    weight: str = "normal",
) -> str:
    spans = "".join(
        f'<tspan x="{x:g}" y="{y + index * line_height:g}">{escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    return (
        f'<text class="{css_class}" x="{x:g}" y="{y:g}" font-family="{FONT_FAMILY}" '
        f'font-size="{size:g}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}">{spans}</text>'
    )


def render_topic_overview(
    topic: str,
    entries: Sequence[OverviewEntry],
    *,
    width: int = WIDTH,
) -> str:
    """A hub-and-spoke figure: the topic above the papers it was briefed on."""
    if not entries:
        return ""

    usable = width - 2 * MARGIN
    box_width = min(MAX_BOX_WIDTH, (usable - GAP * (len(entries) - 1)) / len(entries))
    row_width = box_width * len(entries) + GAP * (len(entries) - 1)
    left = (width - row_width) / 2
    height = LAYOUT_TOP + BOX_HEIGHT

    topic_line = wrap_text(topic, columns=64, max_lines=1)[0]
    topic_width = min(usable, max(200.0, _visual_width(topic_line) * 8.2 + 36))
    topic_x = (width - topic_width) / 2

    rail_y = LAYOUT_TOP - 22
    centers = [left + index * (box_width + GAP) + box_width / 2 for index in range(len(entries))]

    parts: list[str] = [
        f'<svg class="overview" viewBox="0 0 {width} {height}" width="100%" '
        f'height="auto" role="img" aria-label="{escape(topic)}">',
        f'<rect class="ov-topic" x="{topic_x:g}" y="0" width="{topic_width:g}" '
        f'height="{TOPIC_HEIGHT}" rx="8" fill="#eef3fa" stroke="#9db4d0" stroke-width="1" />',
        _text_lines(
            [topic_line],
            x=width / 2,
            y=TOPIC_HEIGHT / 2 + 4,
            line_height=14,
            css_class="ov-topic-text",
            size=11,
            fill=INK,
            anchor="middle",
            weight="bold",
        ),
        f'<path class="ov-line" d="M{width / 2:g},{TOPIC_HEIGHT} V{rail_y:g}" '
        f'fill="none" stroke="{LINE}" stroke-width="1" />',
        f'<path class="ov-line" d="M{centers[0]:g},{rail_y:g} H{centers[-1]:g}" '
        f'fill="none" stroke="{LINE}" stroke-width="1" />',
    ]

    for entry, center in zip(entries, centers, strict=True):
        box_left = center - box_width / 2
        parts.append(
            f'<path class="ov-line" d="M{center:g},{rail_y:g} V{LAYOUT_TOP:g}" '
            f'fill="none" stroke="{LINE}" stroke-width="1" />'
        )
        parts.append(
            f'<rect class="ov-card" x="{box_left:g}" y="{LAYOUT_TOP}" '
            f'width="{box_width:g}" height="{BOX_HEIGHT}" rx="8" '
            f'fill="#ffffff" stroke="#d6d6d6" stroke-width="1" />'
        )
        parts.append(
            f'<circle class="ov-badge" cx="{box_left + 18:g}" '
            f'cy="{LAYOUT_TOP + 18:g}" r="10" fill="#4a6fa5" />'
        )
        parts.append(
            f'<text class="ov-badge-text" x="{box_left + 18:g}" y="{LAYOUT_TOP + 22:g}" '
            f'font-family="{FONT_FAMILY}" font-size="10" font-weight="bold" '
            f'fill="#ffffff" text-anchor="middle">{entry.number}</text>'
        )
        parts.append(
            _text_lines(
                wrap_text(entry.title, columns=_TITLE_CHARS, max_lines=3),
                x=box_left + 34,
                y=LAYOUT_TOP + 20,
                line_height=13,
                css_class="ov-title",
                size=9.5,
                fill=INK,
            )
        )
        meta = " · ".join(
            part
            for part in (
                str(entry.year) if entry.year is not None else "",
                entry.method_family,
            )
            if part
        )
        parts.append(
            _text_lines(
                wrap_text(meta, columns=_META_CHARS, max_lines=1),
                x=box_left + 12,
                y=LAYOUT_TOP + BOX_HEIGHT - 12,
                line_height=12,
                css_class="ov-meta",
                size=8.5,
                fill=MUTED,
            )
        )

    parts.append("</svg>")
    return "".join(parts)
