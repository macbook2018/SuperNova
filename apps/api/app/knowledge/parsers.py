from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from openpyxl import load_workbook
from pptx import Presentation
from pypdf import PdfReader


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    metadata: dict


SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".csv", ".html", ".htm"
}


def validate_document_bytes(filename: str, content_type: str, body: bytes) -> str:
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("unsupported_file_type")
    if not body:
        raise ValueError("empty_file")
    if suffix == ".pdf":
        if not body.startswith(b"%PDF-"):
            raise ValueError("file_signature_mismatch")
        return "application/pdf"
    if suffix in {".docx", ".pptx", ".xlsx"}:
        if not body.startswith(b"PK"):
            raise ValueError("file_signature_mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = archive.infolist()
                names = {item.filename for item in members}
                if len(members) > 10000 or sum(item.file_size for item in members) > 100_000_000:
                    raise ValueError("office_document_expansion_limit_exceeded")
        except zipfile.BadZipFile:
            raise ValueError("invalid_office_document") from None
        required = {
            ".docx": "word/document.xml",
            ".pptx": "ppt/presentation.xml",
            ".xlsx": "xl/workbook.xml",
        }[suffix]
        if required not in names:
            raise ValueError("file_signature_mismatch")
        return {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }[suffix]
    if b"\x00" in body:
        raise ValueError("binary_text_file_not_supported")
    try:
        body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("text_file_must_be_utf8") from None
    if suffix in {".html", ".htm"}:
        return "text/html"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".md":
        return "text/markdown"
    return "text/plain"


def parse_document(
    filename: str, content_type: str, body: bytes, *, strategy: str = "legacy"
) -> list[ExtractedBlock]:
    if strategy == "structured_v1":
        from app.knowledge.structured_parsers import parse_structured

        return parse_structured(filename, body)
    if strategy != "legacy":
        raise ValueError("unsupported_knowledge_strategy")

    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    blocks: list[ExtractedBlock] = []
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(body), strict=False)
        if len(reader.pages) > 500:
            raise ValueError("pdf_page_limit_exceeded")
        for page_index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(ExtractedBlock(text=text, metadata={"page_number": page_index}))
    elif suffix == ".docx":
        document = DocxDocument(io.BytesIO(body))
        paragraph_index = 0
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if text:
                blocks.append(
                    ExtractedBlock(
                        text=text,
                        metadata={
                            "paragraph_index": paragraph_index,
                            "heading": paragraph.style.name if paragraph.style else None,
                        },
                    )
                )
            paragraph_index += 1
        for table_index, table in enumerate(document.tables):
            rows = []
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " / ") for cell in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(
                    ExtractedBlock(
                        text="\n".join(rows),
                        metadata={"table_index": table_index},
                    )
                )
    elif suffix == ".pptx":
        presentation = Presentation(io.BytesIO(body))
        if len(presentation.slides) > 500:
            raise ValueError("pptx_slide_limit_exceeded")
        for slide_index, slide in enumerate(presentation.slides, start=1):
            parts = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    value = shape.text.strip()
                    if value:
                        parts.append(value)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            if parts:
                blocks.append(
                    ExtractedBlock(
                        text="\n".join(parts),
                        metadata={"slide_number": slide_index},
                    )
                )
    elif suffix == ".xlsx":
        workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        try:
            if len(workbook.worksheets) > 100:
                raise ValueError("xlsx_sheet_limit_exceeded")
            total_rows = 0
            for sheet in workbook.worksheets:
                rows = []
                for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                    total_rows += 1
                    if row_index > 10000 or total_rows > 50000:
                        raise ValueError("xlsx_row_limit_exceeded")
                    values = [str(value).strip() for value in row if value is not None]
                    if values:
                        rows.append((row_index, " | ".join(values)))
                if rows:
                    blocks.append(
                        ExtractedBlock(
                            text="\n".join(value for _, value in rows),
                            metadata={
                                "sheet_name": sheet.title,
                                "row_start": rows[0][0],
                                "row_end": rows[-1][0],
                            },
                        )
                    )
        finally:
            workbook.close()
    elif suffix in {".html", ".htm"}:
        soup = BeautifulSoup(body, "html.parser")
        for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
            node.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = soup.get_text("\n", strip=True)
        if text:
            blocks.append(
                ExtractedBlock(text=text, metadata={"title": title or filename})
            )
    elif suffix == ".csv":
        decoded = body.decode("utf-8-sig")
        rows = []
        for row in csv.reader(io.StringIO(decoded)):
            values = [cell.strip() for cell in row]
            if any(values):
                rows.append(" | ".join(values))
        if rows:
            blocks.append(ExtractedBlock(text="\n".join(rows), metadata={"format": "csv"}))
    elif suffix in {".txt", ".md"}:
        text = body.decode("utf-8-sig").strip()
        if text:
            blocks.append(ExtractedBlock(text=text, metadata={"format": suffix[1:]}))
    else:
        raise ValueError("unsupported_file_type")

    cleaned = [
        ExtractedBlock(
            text=block.text.replace("\x00", "").strip(),
            metadata=block.metadata,
        )
        for block in blocks
        if block.text and block.text.strip()
    ]
    if not cleaned:
        raise ValueError("no_extractable_text")
    if sum(len(block.text) for block in cleaned) > 2_000_000:
        raise ValueError("extracted_text_too_large")
    return cleaned
