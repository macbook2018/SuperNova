"""Pack related source blocks, preserving offsets in both the source and the evidence."""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass

from app.knowledge.parsers import ExtractedBlock
from app.knowledge.structured_parsers import FAQ

# Bound index text, source anchors and indexed chunks independently of provider limits.
MAX_SPANS = 64
MAX_CHUNKS = 5000
LOCATION_FIELDS = {
    "page_number",
    "paragraph_index",
    "slide_number",
    "shape_index",
    "sheet_name",
    "table_index",
    "row_number",
    "line_start",
    "line_end",
    "element_index",
    "element_offset",
    "row_start",
    "row_end",
    "table_id",
    "shape_id",
    "cell_range",
}


@dataclass
class SectionRun:
    parent_section_id: str
    heading_path: list[str]
    heading_blocks: list[int]
    question: str
    table_header: str
    blocks: list[tuple[int, ExtractedBlock]]


def _section_runs(blocks: list[ExtractedBlock]) -> Iterator[SectionRun]:
    headings: list[tuple[int, str, int]] = []
    scope = None
    section = "s0"
    question = ""
    table = None
    run = None
    pending_headings = []
    page_scope = False
    previous_kind = ""
    previous_line = None
    for i, block in enumerate(blocks):
        m = block.metadata
        current_scope = m.get("scope", "document")
        kind = m["block_type"]
        if kind == "faq_label":
            continue
        if kind == "paragraph" and (match := FAQ.match(block.text)):
            kind = "question" if match[1].lower() in {"q", "问", "问题"} else "answer"

        if current_scope != scope:
            if run:
                yield run
            next_is_page = "page_number" in m
            if not (page_scope and next_is_page and headings):
                if pending_headings:
                    yield SectionRun(section, [], [], "", "", pending_headings)
                headings, pending_headings, section = [], [], f"s{i}"
            scope, page_scope = current_scope, next_is_page
            run, question, table = None, "", None
        if kind == "heading":
            if run:
                yield run
                run = None
            level = m["heading_level"]
            headings = [h for h in headings if h[0] < level]
            headings.append((level, m.get("heading_text", block.text).strip()[:240], i))
            pending_headings.append((i, block))
            section, question, table = f"s{i}", "", None
            continue

        pending_headings = []
        new_table = m.get("table_id")
        continuous_answer = (
            kind == "paragraph"
            and previous_kind in {"question", "answer"}
            and (previous_line is None or m.get("line_start") == previous_line + 1)
        )
        keep_question = kind == "answer" or continuous_answer
        next_question = m.get("faq_question") or (question if keep_question else "")
        if kind == "question":
            next_question = block.text[:240]
        if run and (kind == "question" or next_question != question or new_table != table):
            yield run
            run = None
        question, table = next_question, new_table
        if run is None:
            path = [h[1] for h in headings]
            title = m.get("slide_title") or m.get("sheet_name")
            if title and title not in path:
                path.insert(0, title[:240])
            run = SectionRun(
                section,
                path,
                [h[2] for h in headings],
                question,
                m.get("table_header", ""),
                [],
            )
        run.blocks.append((i, block))
        previous_kind, previous_line = kind, m.get("line_end")
    if run:
        yield run
    if pending_headings:
        yield SectionRun(section, [], [], "", "", pending_headings)


def _ranges(text: str, size: int, overlap: int, first_size: int) -> Iterator[tuple[int, int]]:
    start = 0
    while start < len(text):
        window = size if start else first_size
        end = min(len(text), start + window)
        if end < len(text):
            floor = start + window * 2 // 3
            for separator in ("\n", "。", "！", "？", ". ", "；", " "):
                position = text.rfind(separator, floor, end)
                if position != -1:
                    end = position + len(separator)
                    break
        yield start, end
        if end == len(text):
            break
        # Upper-limit overlap when a context prefix/sentence break shortens the body.
        start = end - min(overlap, (end - start) // 2)


def _prefix(run: SectionRun, size: int) -> str:
    metadata = run.blocks[0][1].metadata
    document_title = metadata.get("title") or metadata.get("document_title", "")
    path = " > ".join(run.heading_path)
    context = run.table_header or run.question
    # Give the row schema or question room even under a long document/section name.
    budget = size // 3
    if context:
        if path or document_title:
            context = context[: max(1, budget * 2 // 3)]
        else:
            context = context[:budget]
    remaining = budget - len(context) - int(bool(context))
    titles = path if path else document_title
    if path and document_title and document_title != path:
        titles = document_title + " > " + path
    return "\n".join(
        part for part in (titles[-remaining:] if remaining > 0 else "", context) if part
    )


def _pack_run(run: SectionRun, size: int, overlap: int) -> Iterator[dict]:
    prefix = _prefix(run, size)
    budget = size - (len(prefix) + 2 if prefix else 0)
    parts: list[str] = []
    spans: list[dict] = []
    length = 0
    first = None

    def emit() -> dict:
        content = "\n\n".join(parts)
        metadata = {
            **first.metadata,
            "block_index": spans[0]["block_index"],
            "heading_path": run.heading_path,
            "heading_blocks": run.heading_blocks,
            "parent_section_id": run.parent_section_id,
            "source_spans": spans.copy(),
            "parser_version": "structured_v1",
        }
        if run.question:
            metadata["faq_question"] = run.question
        if prefix:
            metadata["index_prefix"] = prefix
        if "line_start" in spans[0]:
            metadata["line_start"] = spans[0]["line_start"]
        if "row_number" in spans[0]:
            metadata["row_start"] = spans[0]["row_number"]
            metadata["row_end"] = spans[-1]["row_number"]
        if "line_end" in spans[-1]:
            metadata["line_end"] = spans[-1]["line_end"]
        return {
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "index_text": f"{prefix}\n\n{content}" if prefix else content,
            "metadata": metadata,
        }

    for i, block in run.blocks:
        source = {k: v for k, v in block.metadata.items() if k in LOCATION_FIELDS}
        line_breaks = [n for n, char in enumerate(block.text) if char == "\n"]
        first_size = budget
        room = budget - length - 2
        question_only = (
            len(spans) == 1 and bool(run.question) and first.text.startswith(run.question)
        )
        if room >= max(8, budget // 5) and (len(block.text) > budget or question_only):
            first_size = min(budget, room) if spans else budget
        for start, end in _ranges(block.text, budget, overlap, first_size):
            value = block.text[start:end]
            if not value.strip():
                continue
            if spans and (length + 2 + len(value) > budget or len(spans) == MAX_SPANS):
                yield emit()
                parts, spans, length = [], [], 0
            if not spans:
                first = block
            else:
                length += 2
            location = source.copy()
            if "page_char_start" in block.metadata:
                origin = block.metadata["page_char_start"]
                location.update(page_char_start=origin + start, page_char_end=origin + end)
            if (
                "line_start" in source
                and source["line_start"] is not None
                and (block.metadata.get("source_format") in {"md", "txt"})
            ):
                origin = source["line_start"]
                location.update(
                    line_start=origin + bisect_left(line_breaks, start),
                    line_end=origin + bisect_left(line_breaks, end - 1),
                )
            spans.append(
                {
                    **location,
                    "block_index": i,
                    "start": start,
                    "end": end,
                    "chunk_start": length,
                    "chunk_end": length + len(value),
                }
            )
            parts.append(value)
            length += len(value)
    if spans:
        yield emit()


def structured_chunks(
    blocks: list[ExtractedBlock], *, chunk_size: int, chunk_overlap: int
) -> list[dict]:
    result = []
    for run in _section_runs(blocks):
        for chunk in _pack_run(run, chunk_size, chunk_overlap):
            if len(result) >= MAX_CHUNKS:
                raise ValueError("document_chunk_limit_exceeded")
            result.append(chunk)
    return result
