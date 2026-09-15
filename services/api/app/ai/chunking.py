"""Deterministic chunking of verified evidence text.

Bump ``CHUNKING_VERSION`` whenever the output for the same input can change; indexed evidence
built with another version is then reported as stale and excluded from retrieval.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

CHUNKING_VERSION = 1


class ChunkingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    index: int
    kind: str
    text: str
    char_start: int | None = None
    char_end: int | None = None
    json_locations: list[dict[str, Any]] | None = field(default=None)


def decode_evidence_text(content: bytes) -> str:
    """Decode stored evidence exactly as imported (UTF-8, byte order mark kept as a character)."""
    return content.decode("utf-8")


def _boundary(text: str, start: int, end: int) -> int:
    """Pick a natural break in ``text[start:end]``, preferring paragraphs, lines and sentences."""
    floor = start + (end - start) // 2
    for separator in ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "):
        position = text.rfind(separator, floor, end)
        if position >= 0:
            return position + len(separator)
    return end


def chunk_plain_text(text: str, *, target: int, overlap: int, max_chunks: int) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + target, length)
        if end < length:
            end = _boundary(text, start, end)
        piece = text[start:end]
        if piece.strip():
            # Trim surrounding whitespace but keep offsets exact.
            leading = len(piece) - len(piece.lstrip())
            trailing = len(piece) - len(piece.rstrip())
            chunk_start, chunk_end = start + leading, end - trailing
            if len(chunks) >= max_chunks:
                raise ChunkingError(
                    "too_many_chunks",
                    f"evidence needs more than {max_chunks} chunks; raise the indexing limit",
                )
            chunks.append(
                ChunkDraft(
                    index=len(chunks),
                    kind="text",
                    text=text[chunk_start:chunk_end],
                    char_start=chunk_start,
                    char_end=chunk_end,
                )
            )
        if end >= length:
            break
        next_start = max(end - overlap, start + 1)
        # Do not start inside a word.
        while 0 < next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start if next_start < end else end
    return chunks


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _unescape_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def flatten_json(document: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(RFC 6901 pointer, JSON-encoded leaf value)`` in document order."""
    if isinstance(document, dict):
        if not document:
            yield pointer, "{}"
        for key, value in document.items():
            yield from flatten_json(value, f"{pointer}/{_escape_pointer_token(str(key))}")
    elif isinstance(document, list):
        if not document:
            yield pointer, "[]"
        for position, value in enumerate(document):
            yield from flatten_json(value, f"{pointer}/{position}")
    else:
        yield pointer, json.dumps(document, ensure_ascii=False)


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Return the value at ``pointer``; raises ``KeyError`` when it does not exist."""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current = document
    for raw in pointer[1:].split("/"):
        token = _unescape_pointer_token(raw)
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(pointer)
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                raise KeyError(pointer)
            current = current[int(token)]
        else:
            raise KeyError(pointer)
    return current


def chunk_json_text(text: str, *, target: int, max_chunks: int) -> list[ChunkDraft]:
    try:
        document = json.loads(text.removeprefix("﻿"))
    except json.JSONDecodeError as exc:
        raise ChunkingError("invalid_json", "stored JSON evidence could not be parsed") from exc
    chunks: list[ChunkDraft] = []
    buffer: list[str] = []
    locations: list[dict[str, Any]] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, locations, size
        if not buffer:
            return
        if len(chunks) >= max_chunks:
            raise ChunkingError(
                "too_many_chunks",
                f"evidence needs more than {max_chunks} chunks; raise the indexing limit",
            )
        chunks.append(
            ChunkDraft(
                index=len(chunks), kind="json", text="".join(buffer), json_locations=locations
            )
        )
        buffer, locations, size = [], [], 0

    for pointer, value in flatten_json(document):
        label = f"{pointer or '/'}: "
        # Very long values are split across chunks; every piece keeps its pointer.
        pieces = [value[i : i + target] for i in range(0, len(value), target)] or [""]
        for piece in pieces:
            line = f"{label}{piece}\n"
            if size and size + len(line) > target:
                flush()
            start = size
            buffer.append(line)
            size += len(line)
            locations.append({"pointer": pointer, "start": start, "end": size - 1})
    flush()
    return chunks


def chunk_evidence(
    kind: str, content: bytes, *, target: int, overlap: int, max_chunks: int
) -> list[ChunkDraft]:
    try:
        text = decode_evidence_text(content)
    except UnicodeDecodeError as exc:
        raise ChunkingError("invalid_encoding", "stored evidence is not valid UTF-8") from exc
    if kind == "json":
        return chunk_json_text(text, target=target, max_chunks=max_chunks)
    return chunk_plain_text(text, target=target, overlap=overlap, max_chunks=max_chunks)


def pointer_at(json_locations: list[dict[str, Any]], offset: int) -> str | None:
    """The JSON pointer whose line contains ``offset`` within a JSON chunk's text."""
    for location in json_locations:
        if int(location["start"]) <= offset <= int(location["end"]):
            return str(location["pointer"])
    return None
