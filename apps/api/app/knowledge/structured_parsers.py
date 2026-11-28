"""Ordered source blocks for the opt-in structured knowledge index."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from itertools import groupby
from pathlib import PurePosixPath

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.knowledge.parsers import ExtractedBlock

ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|[0-9]+[.)]\s+)")
FAQ = re.compile(r"^\s*(?:\*\*)?(问(?:题)?|答(?:案)?|[QA])\s*\d*\s*[:：]", re.I)
CHAPTER = re.compile(r"^第[〇零一二三四五六七八九十百千万两\d]+([章篇节])(?:\s|[、：:])?.{0,55}$")
TABLE_RULE = re.compile(r"^:?-{3,}:?$")
MAX_BLOCKS = 50000
MAX_CHARACTERS = 2_000_000


def _cells(line: str) -> list[str]:
    return [value.strip() for value in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _text_blocks(text: str, markdown: bool) -> Iterator[ExtractedBlock]:
    lines = text.splitlines()
    pending: list[str] = []
    start = 0

    def paragraph(end: int) -> ExtractedBlock:
        return ExtractedBlock(
            "\n".join(pending),
            {
                "block_type": "paragraph",
                "line_start": start + 1,
                "line_end": end,
            },
        )

    i = 0
    while i < len(lines):
        line = lines[i]
        kind = "paragraph"
        meta = {}
        end = i + 1
        if not line.strip():
            kind = "blank"
        elif markdown and (fence := FENCE.match(line)):
            kind = "code"
            token = fence[1]
            close = re.compile(r"^ {0,3}" + token[0] + "{" + str(len(token)) + r",}\s*$")
            while end < len(lines):
                end += 1
                if close.match(lines[end - 1]):
                    break
        elif markdown and (heading := ATX.match(line)):
            kind = "heading"
            meta = {"heading_level": len(heading[1]), "heading_text": heading[2].strip()}
        elif markdown and i + 1 < len(lines) and (rule := SETEXT.match(lines[i + 1])):
            kind = "heading"
            meta = {
                "heading_level": 1 if rule[1].startswith("=") else 2,
                "heading_text": line.strip(),
            }
            end += 1
        elif (
            markdown
            and "|" in line
            and i + 1 < len(lines)
            and "|" in lines[i + 1]
            and all(TABLE_RULE.fullmatch(cell) for cell in _cells(lines[i + 1]))
            and len(_cells(lines[i + 1])) == len(_cells(line))
        ):
            if pending:
                yield paragraph(i)
                pending = []
            table = {
                "block_type": "table_row",
                "table_id": f"md:{i + 1}",
                "table_header": line[:1024],
            }
            yield ExtractedBlock(
                line,
                {
                    **table,
                    "row_number": 1,
                    "is_table_header": True,
                    "line_start": i + 1,
                    "line_end": i + 1,
                },
            )
            end += 1
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                yield ExtractedBlock(
                    lines[end],
                    {
                        **table,
                        "row_number": end - i,
                        "is_table_header": False,
                        "line_start": end + 1,
                        "line_end": end + 1,
                    },
                )
                end += 1
            i = end
            continue
        elif not markdown and (heading := CHAPTER.fullmatch(line.strip())):
            kind = "heading"
            meta = {"heading_level": 2 if heading[1] == "节" else 1}
        elif match := FAQ.match(line):
            kind = "question" if match[1].lower() in {"q", "问", "问题"} else "answer"
        elif markdown and LIST_ITEM.match(line):
            kind = "list_item"

        if kind != "paragraph" and pending:
            yield paragraph(i)
            pending = []
        if kind == "paragraph":
            if not pending:
                start = i
            pending.append(line)
        elif kind != "blank":
            yield ExtractedBlock(
                "\n".join(lines[i:end]),
                {
                    **meta,
                    "block_type": kind,
                    "line_start": i + 1,
                    "line_end": end,
                },
            )
        i = end
    if pending:
        yield paragraph(len(lines))


def _csv_blocks(text: str) -> Iterator[ExtractedBlock]:
    rows = csv.reader(io.StringIO(text, newline=""))
    previous = 0
    header = None
    width = 0
    for number, row in enumerate(rows, 1):
        if number > 50000 or len(row) > 16384:
            raise ValueError("csv_table_limit_exceeded")
        start, previous = previous + 1, rows.line_num
        if not any(cell.strip() for cell in row):
            continue
        first = header is None
        if first:
            width = len(row)
        if len(row) < width:
            row.extend([""] * (width - len(row)))
        value = " | ".join(row)
        if first:
            header = value[:1024]
        yield ExtractedBlock(
            value,
            {
                "block_type": "table_row",
                "table_id": "csv:1",
                "table_header": header,
                "is_table_header": first,
                "row_number": number,
                "line_start": start,
                "line_end": previous,
            },
        )


def _html_blocks(text: str) -> Iterator[ExtractedBlock]:
    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    root = soup.body or soup
    # Element traversal is iterative so nested containers keep document order.
    ignored = {"head", "script", "style", "noscript", "svg", "nav", "footer", "template", "title"}
    boundaries = {
        "article",
        "section",
        "header",
        "div",
        "main",
        "ul",
        "ol",
        "blockquote",
        "li",
        "p",
        "dt",
        "dd",
        "br",
        "dl",
        "hr",
    }
    token = object()

    def events():
        stack = [root]
        while stack:
            node = stack.pop()
            if node is token:
                yield token, None
            elif isinstance(node, Comment):
                continue
            elif isinstance(node, NavigableString):
                yield "text", str(node)
            elif isinstance(node, Tag) and node.name not in ignored:
                if node.name in {"h1", "h2", "h3", "h4", "h5", "h6", "table", "pre"}:
                    yield node.name, node
                else:
                    if node.name in boundaries:
                        yield token, None
                        stack.append(token)
                    stack.extend(reversed(node.contents))

    for number, (kind, group) in enumerate(groupby(events(), key=lambda item: item[0]), 1):
        base = {"element_index": number, "title": title[:240]}
        if kind is token:
            continue
        if kind == "text":
            value = "".join(value for _, value in group).strip()
            if value:
                yield ExtractedBlock(value, {**base, "block_type": "paragraph"})
            continue
        for offset, (_, node) in enumerate(group):
            location = {**base, "element_offset": offset, "line_start": node.sourceline}
            if kind == "table":
                header = None
                for row_number, row in enumerate(node.find_all("tr"), 1):
                    if row.find_parent("table") is not node:
                        continue
                    cells = [
                        c.get_text(" ", strip=True)
                        for c in row.find_all(["th", "td"], recursive=False)
                    ]
                    if not any(cells):
                        continue
                    value = " | ".join(cells)
                    first = header is None
                    if first:
                        header = value[:1024]
                    yield ExtractedBlock(
                        value,
                        {
                            **location,
                            "block_type": "table_row",
                            "row_number": row_number,
                            "table_id": f"html:{number}:{offset}",
                            "table_header": header,
                            "is_table_header": first,
                        },
                    )
            elif kind == "pre":
                yield ExtractedBlock(node.get_text(), {**location, "block_type": "code"})
            else:
                yield ExtractedBlock(
                    node.get_text(" ", strip=True),
                    {
                        **location,
                        "block_type": "heading",
                        "heading_level": int(kind[1]),
                    },
                )


def _link_published_faq(blocks: list[ExtractedBlock]) -> None:
    """The review workflow publishes one canonical question and answer under sibling headings."""
    level_two = [
        (i, b.metadata["heading_text"])
        for i, b in enumerate(blocks)
        if b.metadata.get("heading_level") == 2
    ]
    if (
        blocks[0].metadata.get("heading_level") != 1
        or len(level_two) < 3
        or level_two[0] != (1, "常见问题")
        or level_two[1][1] != "标准答复"
        or level_two[-1][1] != "分类"
    ):
        return
    question_start = level_two[0][0] + 1
    answer_label = level_two[1][0]
    end = next(i for i, title in level_two[2:] if title in {"相关问法", "适用时间", "分类"})
    if answer_label <= question_start or end <= answer_label + 1:
        return
    question = blocks[question_start].text[:240]
    for i in range(level_two[0][0], end):
        metadata = blocks[i].metadata
        if i in {level_two[0][0], answer_label}:
            metadata["block_type"] = "faq_label"
        else:
            metadata.update(faq_question=question, faq_question_block=question_start)
            if i == question_start:
                metadata["block_type"] = "question"


def parse_structured(filename: str, body: bytes) -> list[ExtractedBlock]:
    path = PurePosixPath(filename.replace("\\", "/"))
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".docx", ".pptx", ".xlsx"}:
        from app.knowledge.structured_office import office_blocks

        blocks = office_blocks(suffix, body)
    elif suffix in {".html", ".htm", ".csv", ".txt", ".md"}:
        text = body.decode("utf-8-sig")
        if suffix in {".html", ".htm"}:
            blocks = _html_blocks(text)
        elif suffix == ".csv":
            blocks = _csv_blocks(text)
        else:
            blocks = _text_blocks(text, markdown=suffix == ".md")
    else:
        raise ValueError("unsupported_file_type")

    result: list[ExtractedBlock] = []
    characters = 0
    for block in blocks:
        value = block.text.replace("\x00", "")
        if not value.strip():
            continue
        characters += len(value)
        if characters > MAX_CHARACTERS or len(result) >= MAX_BLOCKS:
            raise ValueError("extracted_text_too_large")
        result.append(
            ExtractedBlock(
                value,
                {
                    "source_format": suffix[1:],
                    "document_title": path.stem[:240],
                    **block.metadata,
                },
            )
        )
    if not result:
        raise ValueError("no_extractable_text")
    if suffix == ".md":
        _link_published_faq(result)
    return result
