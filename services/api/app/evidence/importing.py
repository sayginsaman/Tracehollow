"""Validation for bounded text and JSON evidence imports.

The stored bytes are exactly the uploaded bytes; validation only decides whether they are
accepted. Browser-declared MIME types and filenames are not trusted.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.evidence.models import EvidenceKind

MAX_FILENAME_BYTES = 255
CONTENT_TYPES = {
    EvidenceKind.TEXT: "text/plain; charset=utf-8",
    EvidenceKind.JSON: "application/json",
}


class ImportRejectedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SanitizedFilename:
    value: str | None
    changed: bool


def sanitize_filename(raw: str | None) -> SanitizedFilename:
    """Reduce a client filename to a display-only base name.

    Path components, control and bidirectional formatting characters, and leading dots are
    removed. The result is never used to build a storage path.
    """
    if raw is None or raw == "":
        return SanitizedFilename(None, False)
    name = unicodedata.normalize("NFKC", raw)
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = name.strip().lstrip(".").strip()
    if not name:
        return SanitizedFilename("unnamed", True)
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_FILENAME_BYTES:
        stem, dot, extension = name.rpartition(".")
        suffix = f".{extension}" if dot and len(extension) <= 16 else ""
        budget = MAX_FILENAME_BYTES - len(suffix.encode("utf-8"))
        base = (stem if suffix else name).encode("utf-8")[:budget].decode("utf-8", "ignore")
        name = base + suffix
    return SanitizedFilename(name, name != raw)


def _decode_utf8(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ImportRejectedError("invalid_encoding", "content is not valid UTF-8 text") from None
    if "\x00" in text:
        raise ImportRejectedError("binary_content", "content contains NUL bytes")
    return text


def _reject_constant(value: str) -> Any:
    raise ImportRejectedError("invalid_json", f"JSON constant {value} is not allowed")


def _check_depth(document: Any, max_depth: int) -> None:
    stack: list[tuple[Any, int]] = [(document, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            if depth > max_depth:
                raise ImportRejectedError(
                    "json_too_deep", f"JSON nesting exceeds the limit of {max_depth} levels"
                )
            children = node.values() if isinstance(node, dict) else node
            stack.extend((child, depth + 1) for child in children)


def validate_content(kind: EvidenceKind, content: bytes, *, max_json_depth: int) -> None:
    if kind not in CONTENT_TYPES:
        raise ImportRejectedError(
            "unsupported_kind",
            "This form imports text or JSON. Use the WhatsApp export or document import for "
            "archives and PDF files.",
        )
    if not content:
        raise ImportRejectedError("empty_content", "the uploaded file is empty")
    text = _decode_utf8(content)
    if kind == EvidenceKind.JSON:
        try:
            document = json.loads(text.removeprefix("﻿"), parse_constant=_reject_constant)
        except RecursionError:
            raise ImportRejectedError(
                "json_too_deep", f"JSON nesting exceeds the limit of {max_json_depth} levels"
            ) from None
        except json.JSONDecodeError as exc:
            raise ImportRejectedError(
                "invalid_json", f"content is not valid JSON (line {exc.lineno}, column {exc.colno})"
            ) from None
        _check_depth(document, max_json_depth)
