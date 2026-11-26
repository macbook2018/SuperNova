"""Ordered, source-addressable blocks in Office documents and text PDFs."""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Iterator

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pypdf import PdfReader

from app.knowledge.parsers import ExtractedBlock

_MAX_TABLE_HEADER_CHARS = 1024
_MAX_XLSX_COLUMNS = 1024
_MAX_XLSX_CELLS = 1_000_000
_PDF_HEADING = re.compile(
    r"(?:第[〇零一二三四五六七八九十百千万两0-9]{1,8}(?P<cn>章|节)"
    r"(?:[\s:：—-]+[^，。；！？,;.!?]{1,60})?"
    r"|(?P<en>(?i:chapter|section))\s+[0-9]{1,4}"
    r"(?:\s*[:—–-]\s*[^，。；！？,;.!?]{1,60}|\s+[A-Z][^，。；！？,;.!?]{0,59})?)"
)


def _table_blocks(
    rows: Iterable[tuple[int, list[str]]], table_id: str, location: dict
) -> Iterator[ExtractedBlock]:
    header = None
    header_width = 0
    for row_number, cells in rows:
        if not any(cells):
            continue
        is_header = header is None
        if is_header:
            header_width = len(cells)
        elif len(cells) < header_width:
            cells += [""] * (header_width - len(cells))
        text = " | ".join(cells)
        if is_header:
            # The source block always retains the full row; bound repeated context.
            header = text[:_MAX_TABLE_HEADER_CHARS]
        yield ExtractedBlock(
            text,
            {
                **location,
                "block_type": "table_row",
                "table_id": table_id,
                "table_header": header,
                "is_table_header": is_header,
                "row_number": row_number,
                "row_start": row_number,
                "row_end": row_number,
            },
        )


def _pdf_parts(text: str) -> Iterator[tuple[int, int, int | None]]:
    """Return offsets into the extracted page. Ordinary wrapped lines stay together."""
    offset = 0
    start = None
    end = 0
    for line in text.splitlines(keepends=True):
        value = line.strip()
        heading = _PDF_HEADING.fullmatch(value) if len(value) <= 80 else None
        line_start = offset + len(line) - len(line.lstrip())
        offset += len(line)
        if not value or heading:
            if start is not None:
                yield start, end, None
                start = None
            if heading:
                label = heading["cn"] or heading["en"].lower()
                level = 1 if label in {"章", "chapter"} else 2
                yield line_start, line_start + len(value), level
        else:
            if start is None:
                start = line_start
            end = line_start + len(value)
    if start is not None:
        yield start, end, None


def _pdf_blocks(body: bytes) -> Iterator[ExtractedBlock]:
    reader = PdfReader(io.BytesIO(body), strict=False)
    if len(reader.pages) > 500:
        raise ValueError("pdf_page_limit_exceeded")
    for page_number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        for index, (start, end, level) in enumerate(_pdf_parts(text)):
            metadata = {
                "scope": f"page:{page_number}",
                "block_type": "paragraph",
                "page_number": page_number,
                "paragraph_index": index,
                "page_char_start": start,
                "page_char_end": end,
            }
            if level is not None:
                metadata.update(block_type="heading", heading_level=level)
            yield ExtractedBlock(text[start:end], metadata)


def _word_heading_level(paragraph: Paragraph) -> int | None:
    # An explicit outline level also works with custom or localized style names.
    props = paragraph._p.pPr
    if props is not None:
        outline = props.find(qn("w:outlineLvl"))
        if outline is not None:
            raw = outline.get(qn("w:val"), "")
            if raw.isdigit() and 0 <= int(raw) <= 8:
                return int(raw) + 1
            return None
    style = paragraph.style
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        for label in (style.style_id, style.name):
            if label in {"Title", "标题"}:
                return 1
            heading = re.fullmatch(r"(?:Heading|标题)\s*([1-9])", label or "", re.I)
            if heading:
                return int(heading[1])
        style = style.base_style
    return None


def _word_table_rows(table: Table) -> Iterator[tuple[int, list[str]]]:
    for number, row in enumerate(table.rows, 1):
        # GridBefore/After denote omitted physical cells, unlike empty cells.
        before = row._tr.xpath("./w:trPr/w:gridBefore/@w:val")
        after = row._tr.xpath("./w:trPr/w:gridAfter/@w:val")
        leading = int(before[0]) if before else 0
        trailing = int(after[0]) if after else 0
        spans = [cell.grid_span for cell in row._tr.tc_lst]
        if min(leading, trailing) < 0 or any(span <= 0 for span in spans):
            raise ValueError("invalid_docx_table_grid")
        if leading + sum(spans) + trailing > 1024:
            raise ValueError("docx_column_limit_exceeded")
        cells = [""] * leading
        for cell, span in zip(row._tr.tc_lst, spans, strict=True):
            cells.append(_Cell(cell, table).text.strip())
            cells.extend([""] * (span - 1))
        yield number, cells + [""] * trailing


def _docx_blocks(body: bytes) -> Iterator[ExtractedBlock]:
    document = DocxDocument(io.BytesIO(body))
    paragraph_index = table_index = 0
    for element in document.element.body:
        if isinstance(element, CT_P):
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if text:
                metadata = {
                    "scope": "document",
                    "block_type": "paragraph",
                    "paragraph_index": paragraph_index,
                }
                level = _word_heading_level(paragraph)
                if level is not None:
                    metadata.update(block_type="heading", heading_level=level)
                yield ExtractedBlock(text, metadata)
            paragraph_index += 1
        elif isinstance(element, CT_Tbl):
            table = Table(element, document)
            yield from _table_blocks(
                _word_table_rows(table),
                f"table:{table_index}",
                {"scope": "document", "table_index": table_index},
            )
            table_index += 1


def _pptx_blocks(body: bytes) -> Iterator[ExtractedBlock]:
    presentation = Presentation(io.BytesIO(body))
    if len(presentation.slides) > 500:
        raise ValueError("pptx_slide_limit_exceeded")
    for slide_number, slide in enumerate(presentation.slides, 1):
        scope = f"slide:{slide_number}"
        location = {"scope": scope, "slide_number": slide_number}
        title = slide.shapes.title
        if title is not None and title.text.strip():
            location["slide_title"] = title.text.strip()[:_MAX_TABLE_HEADER_CHARS]
        # A group is stored at one position in the shape sequence. Walk its
        # children there without discarding group text or adding recursion depth.
        stack = [iter(slide.shapes)]
        while stack:
            shape = next(stack[-1], None)
            if shape is None:
                stack.pop()
                continue
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                stack.append(iter(shape.shapes))
                continue
            shape_location = {**location, "shape_id": shape.shape_id}
            if shape.has_table:
                rows = (
                    (number, [cell.text.strip() for cell in row.cells])
                    for number, row in enumerate(shape.table.rows, 1)
                )
                yield from _table_blocks(rows, f"{scope}:shape:{shape.shape_id}", shape_location)
            elif shape.has_text_frame:
                if title is not None and shape.shape_id == title.shape_id:
                    if shape.text.strip():
                        yield ExtractedBlock(
                            shape.text.strip(),
                            {
                                **shape_location,
                                "block_type": "heading",
                                "heading_level": 1,
                            },
                        )
                    continue
                for index, paragraph in enumerate(shape.text_frame.paragraphs):
                    if paragraph.text.strip():
                        yield ExtractedBlock(
                            paragraph.text.strip(),
                            {
                                **shape_location,
                                "block_type": "paragraph",
                                "paragraph_index": index,
                            },
                        )


def _xlsx_blocks(body: bytes) -> Iterator[ExtractedBlock]:
    workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) > 100:
            raise ValueError("xlsx_sheet_limit_exceeded")
        total_rows = total_cells = 0

        def sheet_rows(sheet):
            nonlocal total_rows, total_cells
            for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                total_rows += 1
                if number > 10_000 or total_rows > 50_000:
                    raise ValueError("xlsx_row_limit_exceeded")
                if len(row) > _MAX_XLSX_COLUMNS:
                    raise ValueError("xlsx_column_limit_exceeded")
                total_cells += len(row)
                if total_cells > _MAX_XLSX_CELLS:
                    raise ValueError("xlsx_cell_limit_exceeded")
                yield number, ["" if value is None else str(value).strip() for value in row]

        for sheet_index, sheet in enumerate(workbook.worksheets, 1):
            scope = f"sheet:{sheet_index}"
            # Producer-supplied dimensions can be wrong. Stream actual rows,
            # including internal row/column gaps, rather than padding the entire grid.
            sheet.reset_dimensions()
            yield from _table_blocks(
                sheet_rows(sheet), scope, {"scope": scope, "sheet_name": sheet.title}
            )
    finally:
        workbook.close()


def office_blocks(suffix: str, body: bytes) -> Iterator[ExtractedBlock]:
    parser = {
        ".pdf": _pdf_blocks,
        ".docx": _docx_blocks,
        ".pptx": _pptx_blocks,
        ".xlsx": _xlsx_blocks,
    }.get(suffix)
    if parser is None:
        raise ValueError("unsupported_file_type")
    yield from parser(body)
