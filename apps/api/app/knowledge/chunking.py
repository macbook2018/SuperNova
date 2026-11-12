from __future__ import annotations

import hashlib
import re

from app.knowledge.parsers import ExtractedBlock


def _split_block(text: str, chunk_size: int, overlap: int) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", text).strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    text_length = len(normalized)
    preferred_breaks = ("\n", "。", "！", "？", ". ", "! ", "? ", ";", "；", " ")
    while start < text_length:
        end = min(text_length, start + chunk_size)
        if end < text_length:
            floor = start + max(1, int(chunk_size * 0.65))
            for separator in preferred_breaks:
                candidate = normalized.rfind(separator, floor, end)
                if candidate >= floor:
                    end = candidate + len(separator)
                    break
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= text_length:
            break
        next_start = max(start + 1, end - overlap)
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def chunk_blocks(
    blocks: list[ExtractedBlock],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict]:
    if chunk_size < 100 or chunk_size > 4000:
        raise ValueError("chunk_size_out_of_range")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap_out_of_range")

    chunks: list[dict] = []
    for block_index, block in enumerate(blocks):
        for piece in _split_block(block.text, chunk_size, chunk_overlap):
            metadata = dict(block.metadata)
            metadata["block_index"] = block_index
            chunks.append(
                {
                    "content": piece,
                    "content_hash": hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                    "metadata": metadata,
                }
            )
    return chunks
