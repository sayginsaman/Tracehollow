"""PDF work that runs in a short-lived, resource-limited child process.

The parent (:mod:`app.imports.documents`) starts ``python -m app.imports.pdf_worker <command>``
with the document on standard input, and reads one JSON object (or, for ``render``, a PGM image)
from standard output. Address space, CPU time and open files are limited before the interpreter
starts, and the parent enforces a wall-clock timeout, so a hostile or pathological file can stop
one bounded step, not the worker.

Neither library used here fetches external resources: pypdf only parses the bytes given, and
PDFium is not given a network-capable file access callback. The worker service also has no route
to the internet.

Commands:

``inspect``
    Page count, encryption state and PDF features that affect extraction.
``extract --first N --last M --max-chars C``
    Text-layer extraction for pages N..M (1-based, inclusive), each page capped at C characters.
``render --page N --dpi D --max-pixels P``
    One page rasterised in greyscale as a binary PGM image, for OCR.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from typing import Any

MAX_INPUT_BYTES = 256 * 1024 * 1024
# A page whose text layer has fewer visible characters than this counts as having no text.
MIN_TEXT_CHARS = 16
_MAX_XOBJECT_DEPTH = 4


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _read_input() -> bytes:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        _emit({"ok": False, "state": "unsupported", "code": "input_too_large"})
        raise SystemExit(0)
    return data


def _open_reader(data: bytes) -> tuple[Any, dict[str, Any]]:
    from pypdf import PasswordType, PdfReader
    from pypdf.errors import DependencyError, FileNotDecryptedError, PdfReadError, PyPdfError

    info: dict[str, Any] = {"encrypted": False, "opened_with_empty_password": False}
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            info["encrypted"] = True
            try:
                result = reader.decrypt("")
            except (DependencyError, NotImplementedError) as exc:
                return None, {
                    **info,
                    "ok": False,
                    "state": "unsupported",
                    "code": "unsupported_encryption",
                    "detail": type(exc).__name__,
                }
            if result == PasswordType.NOT_DECRYPTED:
                return None, {
                    **info,
                    "ok": False,
                    "state": "encrypted",
                    "code": "password_required",
                }
            info["opened_with_empty_password"] = True
        return reader, info
    except FileNotDecryptedError:
        return None, {**info, "ok": False, "state": "encrypted", "code": "password_required"}
    except (PdfReadError, PyPdfError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return None, {
            **info,
            "ok": False,
            "state": "malformed",
            "code": "unreadable_pdf",
            "detail": type(exc).__name__,
        }


def _inspect(data: bytes) -> dict[str, Any]:
    reader, info = _open_reader(data)
    if reader is None:
        return info
    try:
        pages = len(reader.pages)
        root = reader.trailer["/Root"].get_object()
        acroform = root.get("/AcroForm")
        has_xfa = bool(acroform is not None and "/XFA" in acroform.get_object())
        header = reader.pdf_header
    except Exception as exc:
        return {
            **info,
            "ok": False,
            "state": "malformed",
            "code": "unreadable_page_tree",
            "detail": type(exc).__name__,
        }
    if pages == 0:
        return {**info, "ok": False, "state": "unsupported", "code": "no_pages"}
    return {**info, "ok": True, "pages": pages, "has_xfa": has_xfa, "pdf_header": header}


def _has_images(resources: Any, depth: int = 0) -> bool:
    """Whether page resources reference an image XObject (without decoding any image)."""
    if resources is None or depth > _MAX_XOBJECT_DEPTH:
        return False
    try:
        resources = resources.get_object()
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return False
        for ref in xobjects.get_object().values():
            obj = ref.get_object()
            subtype = obj.get("/Subtype")
            if subtype == "/Image":
                return True
            if subtype == "/Form" and _has_images(obj.get("/Resources"), depth + 1):
                return True
    except Exception:
        return False
    return False


def _extract(data: bytes, first: int, last: int, max_chars: int) -> dict[str, Any]:
    reader, info = _open_reader(data)
    if reader is None:
        return info
    pages: list[dict[str, Any]] = []
    total = len(reader.pages)
    for number in range(first, min(last, total) + 1):
        entry: dict[str, Any] = {"page": number, "text": "", "chars": 0, "truncated": False}
        try:
            page = reader.pages[number - 1]
            entry["has_images"] = _has_images(page.get("/Resources"))
            text = page.extract_text() or ""
        except Exception as exc:
            entry["error"] = "extraction_error"
            entry["error_type"] = type(exc).__name__
            pages.append(entry)
            continue
        text = text.replace("\x00", "")
        if len(text) > max_chars:
            text = text[:max_chars]
            entry["truncated"] = True
        entry["text"] = text
        entry["chars"] = len(text)
        entry["visible_chars"] = sum(1 for ch in text if not ch.isspace())
        pages.append(entry)
    return {**info, "ok": True, "pages": pages}


def _render(data: bytes, page_number: int, dpi: int, max_pixels: int) -> None:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        if not 1 <= page_number <= len(document):
            _emit({"ok": False, "code": "page_out_of_range"})
            return
        page = document[page_number - 1]
        width, height = page.get_size()
        scale = dpi / 72.0
        pixels = (width * scale) * (height * scale)
        if pixels > max_pixels:
            scale *= (max_pixels / pixels) ** 0.5
        bitmap = page.render(scale=scale, grayscale=True, may_draw_forms=False)
        buffer = bytes(bitmap.buffer)
        w, h, stride, channels = bitmap.width, bitmap.height, bitmap.stride, bitmap.n_channels
        rows = []
        for y in range(h):
            row = buffer[y * stride : y * stride + w * channels]
            # Greyscale rendering still uses a colour pixel format; any channel holds the grey.
            rows.append(row if channels == 1 else row[::channels])
        sys.stdout.buffer.write(f"P5\n{w} {h}\n255\n".encode("ascii"))
        sys.stdout.buffer.write(b"".join(rows))
        sys.stdout.buffer.flush()
    finally:
        document.close()


def main(argv: list[str] | None = None) -> int:
    logging.disable(logging.CRITICAL)  # library warnings would only reveal document content
    parser = argparse.ArgumentParser(prog="pdf_worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    extract = sub.add_parser("extract")
    extract.add_argument("--first", type=int, required=True)
    extract.add_argument("--last", type=int, required=True)
    extract.add_argument("--max-chars", type=int, required=True)
    render = sub.add_parser("render")
    render.add_argument("--page", type=int, required=True)
    render.add_argument("--dpi", type=int, required=True)
    render.add_argument("--max-pixels", type=int, required=True)
    args = parser.parse_args(argv)

    data = _read_input()
    try:
        if args.command == "inspect":
            _emit(_inspect(data))
        elif args.command == "extract":
            _emit(_extract(data, args.first, args.last, args.max_chars))
        else:
            _render(data, args.page, args.dpi, args.max_pixels)
    except MemoryError:
        _emit({"ok": False, "code": "resource_limit"})
    except RecursionError:
        _emit({"ok": False, "state": "malformed", "code": "structure_too_deep"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
