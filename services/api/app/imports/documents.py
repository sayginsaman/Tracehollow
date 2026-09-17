"""Bounded document operations for the worker: PDF child processes and optional Tesseract OCR.

Every call starts a fresh child with a minimal environment, a wall-clock timeout, and (where the
operating system enforces them) address-space, CPU-time and open-file limits. Tesseract is an
optional binary: when it or a requested language is missing, :func:`ocr_availability` says so and
processing records an actionable state instead of failing the whole document.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import pypdf
import pypdfium2

from app.config import Settings

API_ROOT = Path(__file__).resolve().parents[2]
PARSER_VERSIONS = {
    "pypdf": pypdf.__version__,
    "pypdfium2": str(pypdfium2.PYPDFIUM_INFO),
    "pdfium": str(pypdfium2.PDFIUM_INFO),
}
_MAX_JSON_OUTPUT = 64 * 1024 * 1024
_OCR_ENABLE_HINT = (
    "Build the worker image with OCR support (INSTALL_OCR=true, see docs/operations/"
    "document-processing.md), restart the worker, then process the document again."
)


class ChildFailedError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _limits(memory_mb: int, cpu_seconds: int) -> Any:
    def apply() -> None:
        import resource

        for limit, value in (
            (resource.RLIMIT_AS, memory_mb * 1024 * 1024),
            (resource.RLIMIT_CPU, cpu_seconds),
            (resource.RLIMIT_NOFILE, 64),
            (resource.RLIMIT_CORE, 0),
        ):
            # macOS does not enforce every limit; the wall-clock timeout still applies.
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(limit, (value, value))

    return apply


def _child_env() -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "OMP_THREAD_LIMIT": "1",
        "HOME": "/nonexistent",
    }


def run_child(
    arguments: list[str],
    stdin: bytes,
    *,
    timeout: int,
    memory_mb: int,
    executable: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> bytes:
    command = [
        *(executable or [sys.executable, "-E", "-s", "-m", "app.imports.pdf_worker"]),
        *arguments,
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument vector, no shell
            command,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            cwd=API_ROOT,
            env=env or _child_env(),
            preexec_fn=_limits(memory_mb, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ChildFailedError("timeout", f"stopped after {timeout} s") from None
    except OSError as exc:
        raise ChildFailedError("spawn_failed", type(exc).__name__) from None
    if completed.returncode < 0:
        raise ChildFailedError("resource_limit", f"terminated by signal {-completed.returncode}")
    if completed.returncode != 0:
        raise ChildFailedError("child_error", f"exit status {completed.returncode}")
    return completed.stdout


def pdf_command(
    settings: Settings, arguments: list[str], document: bytes, *, timeout: int | None = None
) -> dict[str, Any]:
    output = run_child(
        arguments,
        document,
        timeout=timeout or settings.document_parse_timeout_seconds,
        memory_mb=settings.document_parse_memory_mb,
    )
    if len(output) > _MAX_JSON_OUTPUT:
        raise ChildFailedError("output_too_large")
    try:
        payload = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ChildFailedError("child_error", "unreadable output") from None
    if not isinstance(payload, dict):
        raise ChildFailedError("child_error", "unexpected output")
    return payload


def render_page(settings: Settings, document: bytes, page: int) -> bytes:
    output = run_child(
        [
            "render",
            "--page",
            str(page),
            "--dpi",
            str(settings.document_ocr_dpi),
            "--max-pixels",
            str(settings.document_ocr_max_pixels),
        ],
        document,
        timeout=settings.document_ocr_page_timeout_seconds,
        memory_mb=settings.document_parse_memory_mb,
    )
    if output.startswith(b"{"):
        try:
            code = json.loads(output).get("code", "render_failed")
        except ValueError:
            code = "render_failed"
        raise ChildFailedError(str(code))
    if not output.startswith(b"P5\n"):
        raise ChildFailedError("render_failed")
    return output


@dataclass(frozen=True, slots=True)
class OcrAvailability:
    available: bool
    engine_version: str | None
    languages: tuple[str, ...]
    missing_languages: tuple[str, ...] = field(default=())
    reason: str | None = None
    action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "engine": "tesseract",
            "engine_version": self.engine_version,
            "languages": list(self.languages),
            "missing_languages": list(self.missing_languages),
            "reason": self.reason,
            "action": self.action,
        }


def _tesseract_binary() -> str | None:
    return shutil.which("tesseract", path=_child_env()["PATH"])


@cache
def _tesseract_facts(binary: str) -> tuple[str | None, tuple[str, ...]]:
    env = _child_env()
    try:
        version = subprocess.run(  # noqa: S603 - fixed argument vector
            [binary, "--version"], capture_output=True, timeout=20, env=env, check=False
        )
        langs = subprocess.run(  # noqa: S603 - fixed argument vector
            [binary, "--list-langs"], capture_output=True, timeout=20, env=env, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ()
    text = (version.stdout or version.stderr).decode("utf-8", "replace")
    match = re.search(r"tesseract\s+v?([0-9][0-9A-Za-z.\-]*)", text)
    listed = langs.stdout.decode("utf-8", "replace").splitlines()
    return (match.group(1) if match else None), tuple(
        line.strip() for line in listed if re.fullmatch(r"[A-Za-z0-9_]+", line.strip())
    )


def ocr_availability(settings: Settings) -> OcrAvailability:
    requested = tuple(settings.document_ocr_languages.split("+"))
    if not settings.document_ocr_enabled:
        return OcrAvailability(
            False,
            None,
            requested,
            reason="OCR is turned off (TRACEHOLLOW_DOCUMENT_OCR_ENABLED=false).",
            action="Set TRACEHOLLOW_DOCUMENT_OCR_ENABLED=true on the worker to allow OCR.",
        )
    binary = _tesseract_binary()
    if binary is None:
        return OcrAvailability(
            False,
            None,
            requested,
            reason="The Tesseract OCR engine is not installed in the worker.",
            action=_OCR_ENABLE_HINT,
        )
    version, installed = _tesseract_facts(binary)
    missing = tuple(lang for lang in requested if lang not in installed)
    if version is None or missing:
        return OcrAvailability(
            False,
            version,
            requested,
            missing_languages=missing,
            reason=(
                f"Tesseract language data is missing: {', '.join(missing)}."
                if missing
                else "Tesseract did not report its version."
            ),
            action=_OCR_ENABLE_HINT,
        )
    return OcrAvailability(True, version, requested)


def ocr_image(settings: Settings, image: bytes) -> str:
    binary = _tesseract_binary()
    if binary is None:
        raise ChildFailedError("ocr_unavailable")
    output = run_child(
        ["stdin", "stdout", "-l", settings.document_ocr_languages, "--psm", "3"],
        image,
        timeout=settings.document_ocr_page_timeout_seconds,
        memory_mb=settings.document_parse_memory_mb,
        executable=[binary],
    )
    return output.decode("utf-8", "replace").replace("\x00", "").replace("\x0c", "").strip()
