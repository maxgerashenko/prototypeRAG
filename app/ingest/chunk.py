"""Header-aware chunking + metadata (plan/01-crawler.md step 5)."""

import re
from dataclasses import dataclass

_HEADER_RE = re.compile(r"(?m)^#{1,6} .+$")


@dataclass(frozen=True)
class ChunkDraft:
    """One chunk before it's attached to a business/page in store.py."""

    section_heading: str | None
    text: str
    chunk_index: int


def split_by_headers(markdown: str) -> list[tuple[str | None, str]]:
    """Split on ATX headers into (heading, body) pairs; heading is None before the first one."""
    matches = list(_HEADER_RE.finditer(markdown))

    headings: list[str | None] = [None] + [m.group(0).lstrip("#").strip() for m in matches]
    body_starts = [0] + [m.end() for m in matches]
    body_ends = [m.start() for m in matches] + [len(markdown)]

    sections = []
    for heading, start, end in zip(headings, body_starts, body_ends):
        body = markdown[start:end].strip()
        if body:
            sections.append((heading, body))
    return sections


def _hard_split(paragraph: str, target_chars: int) -> list[str]:
    """Split one oversized paragraph on whitespace, packing words up to `target_chars`."""
    words = paragraph.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and current_len + added > target_chars:
            chunks.append(" ".join(current))
            current, current_len = [word], len(word)
        else:
            current.append(word)
            current_len += added
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_text(text: str, target_tokens: int = 650, overlap_ratio: float = 0.125) -> list[str]:
    """Pack `text` into ~target_tokens-sized chunks on paragraph boundaries, with overlap."""
    target_chars = target_tokens * 4  # no tokenizer dependency; a rough approximation
    text = text.strip()
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    paragraphs = [p for p in re.split(r"\n\n+", text) if p.strip()]

    raw_chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > target_chars:
            if current:
                raw_chunks.append(current)
                current = ""
            raw_chunks.extend(_hard_split(para, target_chars))
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= target_chars:
            current = candidate
        else:
            if current:
                raw_chunks.append(current)
            current = para
    if current:
        raw_chunks.append(current)

    overlap_chars = int(target_chars * overlap_ratio)
    if overlap_chars <= 0:
        return raw_chunks
    return [raw_chunks[0]] + [
        f"{_tail_on_word_boundary(raw_chunks[i - 1], overlap_chars)}\n\n{raw_chunks[i]}" for i in range(1, len(raw_chunks))
    ]


def _tail_on_word_boundary(text: str, max_chars: int) -> str:
    """Last <= max_chars of `text`, starting at a word boundary (no half-word at the start)."""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    if not text[-max_chars - 1].isspace():
        _, _, tail = tail.partition(" ")  # drop the cut-off first word
    return tail.strip()


def chunk_markdown(markdown: str, target_tokens: int = 650) -> list[ChunkDraft]:
    """Header-split, then size-split each section; chunk_index runs continuously across the doc."""
    chunks: list[ChunkDraft] = []
    running_index = 0
    for heading, body in split_by_headers(markdown):
        for sub_chunk in chunk_text(body, target_tokens):
            stripped = sub_chunk.strip()
            if not stripped:
                continue
            chunks.append(ChunkDraft(section_heading=heading, text=stripped, chunk_index=running_index))
            running_index += 1
    return chunks
