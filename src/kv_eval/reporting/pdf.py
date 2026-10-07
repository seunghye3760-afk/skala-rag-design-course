"""report.md → report.pdf. 담당 4.

report.py가 만드는 Markdown은 우리가 직접 정한 부분집합이다(제목 #~####, 파이프 테이블,
글머리 `- `, 인용 `> `, 빈 줄로 구분한 문단). 이 모듈은 그 부분집합만 그리는 전용 렌더러이지,
범용 Markdown 파서가 아니다.

한글 폰트: PDF에 폰트를 내장(embed)해야 어떤 컴퓨터·뷰어에서 열어도 글자가 깨지지 않는다.
시스템 폰트를 참조만 하는 CID 폰트 방식은 그 폰트가 없는 컴퓨터·뷰어에서 빈 칸으로
보일 수 있어 쓰지 않는다(poppler 계열에서 "Missing language pack" 오류로 직접 확인).
이 저장소는 폰트 파일을 담지 않으므로 macOS·Linux에 흔한 한글 트루타입 폰트 경로를
순서대로 시도해 처음 등록되는 것을 쓴다. reportlab TTFont는 glyf(트루타입) 외곽선만
읽을 수 있어 Noto Sans CJK 같은 CFF(OpenType) 폰트는 등록에 실패한다 — 그래서 여러
후보를 순서대로 "실제로 등록해보고" 되는 것을 고른다. KV_REPORT_FONT 환경 변수로 경로를
직접 지정할 수 있다. 전부 실패하면 저수준 render_pdf는 None을 반환하고, render_preview는
이를 명시적인 보고서 품질 실패로 바꾼다. 검증되지 않은 report.pdf는 최종 파일명으로 저장하지 않는다.

macOS 후보(AppleGothic·AppleSDGothicNeo)는 이 프로젝트를 만든 클라우드·Linux 환경에서는
실제로 시험해 볼 수 없었다 — 맥에서 `uv run python app.py` 실행 후 report.pdf를 열어
한글이 정상으로 보이는지 직접 확인해야 한다 (안 되면 KV_REPORT_FONT로 다른 폰트 경로 지정).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config import output_root
from ..graph.state import MainState
from ..graph.task_schema import ReportQualityResult
from ..rules.report_quality import MAX_REPORT_PAGES, TARGET_REPORT_PAGES

_FONT_NAME = "KVReportKR"

_CANDIDATES = [
    # macOS (이 순서로 시도 — 트루타입 외곽선일 가능성이 높은 것부터)
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/AppleGothic.ttf",
    os.path.expanduser("~/Library/Fonts/NanumGothic.ttf"),
    "/Library/Fonts/NanumGothic.ttf",
    "/Library/Fonts/NanumGothicCoding.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    # Linux (개발용 이미지·CI에 흔함)
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    # Windows
    "C:\\Windows\\Fonts\\malgun.ttf",
]


def _register_font() -> str | None:
    if _FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return _FONT_NAME
    paths = ([os.environ["KV_REPORT_FONT"]] if os.environ.get("KV_REPORT_FONT") else []) + _CANDIDATES
    for path in paths:
        if not path or not Path(path).exists():
            continue
        for idx in range(4):
            try:
                pdfmetrics.registerFont(TTFont(_FONT_NAME, path, subfontIndex=idx))
                return _FONT_NAME
            except Exception:  # noqa: BLE001, S112 - 후보 폰트/서브폰트를 순서대로 실제 등록해 본다
                continue
    return None


_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_ROW_RE = re.compile(r"^\|(.+)\|$")
_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _inline(text: str) -> str:
    return escape(text).replace("\n", "<br/>")


_MAX_TABLE_CELL_CHARS = 320


def _split_cell(text: str, limit: int = _MAX_TABLE_CELL_CHARS) -> list[str]:
    """Split long table cells so ReportLab can break the table between rows.

    ReportLab can split a Table between rows, but it cannot split a single row
    whose tallest cell is higher than the page. Long evidence lists are
    therefore split at semicolons/newlines first, then at a hard character
    boundary for unusually long individual items.
    """
    text = str(text).strip()
    if len(text) <= limit:
        return [text]

    pieces = re.split(r"(?<=;)\s+|\n", text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        current = piece
    if current or not chunks:
        chunks.append(current)
    return chunks


def _expand_table_rows(rows: list[list[str]]) -> list[list[str]]:
    """Turn long logical rows into page-breakable physical rows."""
    if not rows:
        return []
    column_count = max(len(row) for row in rows)
    expanded: list[list[str]] = []
    for source_row in rows:
        row = list(source_row) + [""] * (column_count - len(source_row))
        columns = [_split_cell(cell) for cell in row]
        row_count = max(len(parts) for parts in columns)
        for row_index in range(row_count):
            physical_row: list[str] = []
            for column_index, parts in enumerate(columns):
                value = parts[row_index] if row_index < len(parts) else ""
                # Keep labels/verdicts on the first continuation row only,
                # while preserving continuation text if those cells are long.
                if row_index and len(parts) == 1:
                    value = ""
                physical_row.append(value)
            expanded.append(physical_row)
    return expanded


def _table_widths(column_count: int, total_width: float) -> list[float]:
    if column_count == 4:
        # The report's comparison table has two evidence-heavy columns.
        first = 44.0
        last = 64.0
        middle = (total_width - first - last) / 2
        return [first, middle, middle, last]
    if column_count == 5:
        fixed = [45.0, 58.0, 105.0, 112.0]
        return [*fixed, total_width - sum(fixed)]
    return [total_width / column_count] * column_count


def _build_story(md_text: str, styles: dict, table_width: float) -> list:
    lines = md_text.split("\n")
    story: list = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _HEADING_RE.match(line)
        if m:
            level, text = len(m.group(1)), m.group(2)
            if level <= 3:
                story.append(CondPageBreak(28 * mm))
            story.append(Spacer(1, 10 if level <= 2 else 6))
            story.append(Paragraph(_inline(text), styles[f"h{min(level, 4)}"]))
            i += 1
            continue
        if _ROW_RE.match(line):
            rows = []
            while i < n and _ROW_RE.match(lines[i]):
                if not _SEP_RE.match(lines[i]):
                    rows.append(_cells(lines[i]))
                i += 1
            if rows:
                rows = _expand_table_rows(rows)
                tbl = Table(
                    [[Paragraph(_inline(c), styles["cell"]) for c in row] for row in rows],
                    colWidths=_table_widths(len(rows[0]), table_width),
                    repeatRows=1,
                    splitByRow=1,
                )
                tbl.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (-1, -1), styles["font"]),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(tbl)
                story.append(Spacer(1, 8))
            continue
        if _BULLET_RE.match(line):
            while i < n and _BULLET_RE.match(lines[i]):
                item = ListItem(Paragraph(_inline(_BULLET_RE.match(lines[i]).group(1)), styles["body"]))
                story.append(ListFlowable([item], bulletType="bullet", leftIndent=14))
                i += 1
            story.append(Spacer(1, 4))
            continue
        if _QUOTE_RE.match(line):
            buf = []
            while i < n and (_QUOTE_RE.match(lines[i]) or not lines[i].strip()):
                if lines[i].strip():
                    buf.append(_QUOTE_RE.match(lines[i]).group(1))
                i += 1
            story.append(Paragraph(_inline("\n".join(buf)), styles["quote"]))
            story.append(Spacer(1, 4))
            continue
        buf = []
        while i < n and lines[i].strip() and not any(
                p.match(lines[i]) for p in (_HEADING_RE, _ROW_RE, _BULLET_RE, _QUOTE_RE)):
            buf.append(lines[i])
            i += 1
        story.append(Paragraph(_inline(" ".join(buf)), styles["body"]))
        story.append(Spacer(1, 4))
    return story


def _styles(font: str) -> dict:
    base = {"fontName": font, "wordWrap": "CJK"}
    return {
        "font": font,
        "h1": ParagraphStyle("h1", fontSize=17, leading=22, spaceAfter=6, keepWithNext=1, **base),
        "h2": ParagraphStyle("h2", fontSize=14, leading=19, spaceAfter=5, keepWithNext=1, **base),
        "h3": ParagraphStyle("h3", fontSize=12, leading=16, spaceAfter=4, keepWithNext=1, **base),
        "h4": ParagraphStyle("h4", fontSize=10.5, leading=14, spaceAfter=3, keepWithNext=1, **base),
        "body": ParagraphStyle("body", fontSize=9.5, leading=14, **base),
        "cell": ParagraphStyle("cell", fontSize=9, leading=11.5, splitLongWords=1, **base),
        "quote": ParagraphStyle("quote", fontSize=9, leading=13, leftIndent=12,
                                textColor=colors.HexColor("#555555"), **base),
    }


def _page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(doc._kv_font, 9)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawCentredString(A4[0] / 2, 9 * mm, f"{doc.page}")
    canvas.restoreState()


def render_pdf(md_text: str, out_path: Path) -> str | None:
    """성공하면 사용한 폰트 이름, 폰트를 못 찾으면 None을 반환한다 (report.pdf는 생략된다).
    그 외 오류(문서 조립 실패 등)는 그대로 올려보낸다 — 호출하는 쪽이 잡는다."""
    font = _register_font()
    if not font:
        return None
    styles = _styles(font)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm)
    doc._kv_font = font
    doc.build(_build_story(md_text, styles, doc.width),
              onFirstPage=_page_footer, onLaterPages=_page_footer)
    return font


def preview_path(state: MainState) -> Path:
    round_ = state.get("report_retry_round", 0)
    return output_root() / state.get("run_id", "run") / f".report-preview-r{round_}.pdf"


def count_pages(path: Path) -> int:
    """렌더링된 PDF의 실제 페이지 수를 pypdf로 센다."""
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


def render_preview(state: MainState) -> dict:
    """최종 파일명과 분리된 PDF를 렌더링하고 실제 페이지 수를 반환한다."""
    path = preview_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    base_quality = state.get("report_quality")
    base_issues = list(base_quality.issues if base_quality else [])
    try:
        font = render_pdf(state["report_draft"], path)
        if not font:
            raise RuntimeError("한글 임베딩 폰트를 찾지 못함")
        page_count = count_pages(path)
    except Exception as error:  # noqa: BLE001 - 렌더링 실패는 품질 실패로 명시한다
        return {
            "report_page_count": 0,
            "report_quality": ReportQualityResult(
                passed=False,
                issues=[*base_issues, f"PDF 미리보기 렌더링 실패: {type(error).__name__}: {error}"],
                retry_kind=None,
            ),
        }

    issues = list(base_issues)
    retry_kind = base_quality.retry_kind if base_quality and not base_quality.passed else None
    passed = bool(base_quality.passed if base_quality else True)
    if page_count > MAX_REPORT_PAGES:
        issues.append(f"PDF가 {page_count}페이지로 제한 {MAX_REPORT_PAGES}페이지를 초과함")
        passed = False
        retry_kind = "compress"
    elif page_count < TARGET_REPORT_PAGES[0]:
        # 8~10페이지는 권장 범위이며, 8페이지 미만은 실패 조건이 아니다.
        retry_kind = None if passed else retry_kind

    return {
        "report_page_count": page_count,
        "report_quality": ReportQualityResult(
            passed=passed,
            page_count=page_count,
            issues=issues,
            retry_kind=retry_kind,
        ),
    }


def persist_preview(state: MainState, final_path: Path) -> None:
    """검증된 preview만 최종 report.pdf 이름으로 승격한다."""
    quality = state.get("report_quality")
    if not quality or not quality.passed or not quality.page_count:
        raise ValueError("품질 검사를 통과한 PDF preview가 필요함")
    if quality.page_count > MAX_REPORT_PAGES:
        raise ValueError(f"최종 PDF는 {MAX_REPORT_PAGES}페이지를 초과할 수 없음")
    source = preview_path(state)
    if not source.exists():
        raise FileNotFoundError(source)
    source.replace(final_path)
    for stale_preview in final_path.parent.glob(".report-preview-r*.pdf"):
        stale_preview.unlink()
