"""Bounded, extraction-free reading of user-provided ZIP archives.

Nothing from an archive is ever written to a path derived from its member names: members are
read into memory one at a time, checked, and handed to the evidence store, which names files by
server-generated UUIDs. Path traversal, absolute paths and symlinks therefore cannot escape
anything; they are still rejected and reported, because they say something about the archive.

Limits (all from settings): member count, declared and actual size per member, total expanded
size, and the expansion ratio per member (decompression bombs). The declared size in a ZIP header
is not trusted: reading stops as soon as a member produces more bytes than it declared.
Encrypted members are not decrypted. Nested archives are kept as files, never expanded.
"""

from __future__ import annotations

import io
import stat
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_READ_CHUNK = 1024 * 1024
# Small members compress extremely well legitimately (runs of spaces); the ratio check applies
# only above this size.
_RATIO_EXEMPT_BYTES = 1024 * 1024


class ArchiveRejectedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int
    max_member_bytes: int
    max_total_bytes: int
    max_ratio: int


@dataclass(frozen=True, slots=True)
class SkippedMember:
    name: str
    reason: str


@dataclass(slots=True)
class ArchiveMember:
    """A regular file read from the archive. ``name`` is the normalized relative path."""

    name: str
    base_name: str
    data: bytes
    compressed_size: int


@dataclass(slots=True)
class ArchiveReport:
    members_listed: int = 0
    files_read: int = 0
    bytes_read: int = 0
    skipped: list[SkippedMember] = field(default_factory=list)


def is_zip(content: bytes) -> bool:
    return content[:4] in (b"PK\x03\x04", b"PK\x05\x06")


def normalize_member_name(raw: str) -> str | None:
    """A safe relative display path, or None when the name is absolute or escapes the root."""
    name = unicodedata.normalize("NFC", raw).replace("\\", "/")
    if "\x00" in name or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [part for part in PurePosixPath(name).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    if any(unicodedata.category(ch)[0] == "C" for part in parts for ch in part):
        return None
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return info.create_system == 3 and stat.S_ISLNK(mode)


def _open(content: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ArchiveRejectedError(
            "invalid_archive", f"not a readable ZIP archive: {exc}"
        ) from None


def _screen(
    infos: list[zipfile.ZipInfo], limits: ArchiveLimits, report: ArchiveReport
) -> list[tuple[zipfile.ZipInfo, str]]:
    """Apply every check that does not need the member's bytes; record what is skipped."""
    report.members_listed = len(infos)
    if len(infos) > limits.max_members:
        raise ArchiveRejectedError(
            "too_many_members",
            f"the archive lists {len(infos)} entries; the limit is {limits.max_members}",
        )
    accepted: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    declared_total = 0
    for info in infos:
        if info.is_dir():
            continue
        name = normalize_member_name(info.filename)
        if name is None:
            report.skipped.append(SkippedMember(info.filename[:300], "unsafe_path"))
        elif _is_symlink(info):
            report.skipped.append(SkippedMember(name, "symbolic_link"))
        elif info.flag_bits & 0x1:
            report.skipped.append(SkippedMember(name, "encrypted_member"))
        elif name.casefold() in seen:
            report.skipped.append(SkippedMember(name, "duplicate_name"))
        elif info.file_size > limits.max_member_bytes:
            report.skipped.append(SkippedMember(name, "member_too_large"))
        elif info.file_size > _RATIO_EXEMPT_BYTES and info.file_size > limits.max_ratio * max(
            info.compress_size, 1
        ):
            report.skipped.append(SkippedMember(name, "expansion_ratio_exceeded"))
        else:
            declared_total += info.file_size
            if declared_total > limits.max_total_bytes:
                raise ArchiveRejectedError(
                    "archive_too_large",
                    f"expanding the archive would exceed {limits.max_total_bytes} bytes",
                )
            seen.add(name.casefold())
            accepted.append((info, name))
    return accepted


def list_regular_names(content: bytes, limits: ArchiveLimits, report: ArchiveReport) -> list[str]:
    """Names of the regular files that would be read, without reading any member."""
    with _open(content) as archive:
        return [name for _info, name in _screen(archive.infolist(), limits, report)]


def read_member(content: bytes, wanted: str, limits: ArchiveLimits) -> bytes | None:
    """The bytes of one accepted member (by normalized name), or None if unusable."""
    report = ArchiveReport()
    with _open(content) as archive:
        for info, name in _screen(archive.infolist(), limits, report):
            if name == wanted:
                return _read_member(archive, info, limits, report, name)
    return None


def iter_members(
    content: bytes, limits: ArchiveLimits, report: ArchiveReport
) -> Iterator[ArchiveMember]:
    """Yield regular files, one in memory at a time, within the limits.

    Raises :class:`ArchiveRejectedError` for an archive that cannot be read at all or that
    exceeds the member count or total size limit; individual unusable members are skipped and
    recorded in ``report``.
    """
    with _open(content) as archive:
        for info, name in _screen(archive.infolist(), limits, report):
            data = _read_member(archive, info, limits, report, name)
            if data is None:
                continue
            report.files_read += 1
            report.bytes_read += len(data)
            yield ArchiveMember(
                name=name,
                base_name=PurePosixPath(name).name,
                data=data,
                compressed_size=info.compress_size,
            )


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limits: ArchiveLimits,
    report: ArchiveReport,
    name: str,
) -> bytes | None:
    budget = min(info.file_size, limits.max_member_bytes)
    buffer = bytearray()
    try:
        with archive.open(info) as handle:
            while chunk := handle.read(_READ_CHUNK):
                buffer.extend(chunk)
                if len(buffer) > budget:
                    report.skipped.append(SkippedMember(name, "size_differs_from_header"))
                    return None
                if report.bytes_read + len(buffer) > limits.max_total_bytes:
                    raise ArchiveRejectedError(
                        "archive_too_large",
                        f"expanding the archive would exceed {limits.max_total_bytes} bytes",
                    )
    except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, RuntimeError) as exc:
        reason = "unsupported_compression" if isinstance(exc, NotImplementedError) else "corrupt"
        report.skipped.append(SkippedMember(name, reason))
        return None
    return bytes(buffer)


def sniff_content_type(data: bytes) -> tuple[str, str]:
    """(evidence kind, descriptive media type) from magic bytes; names and headers are ignored."""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "binary", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "binary", "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "binary", "image/gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "binary", "image/webp"
    if head.startswith(b"OggS"):
        return "binary", "audio/ogg"
    if data[4:8] == b"ftyp":
        return "binary", "video/mp4"
    if is_zip(data):
        return "binary", "application/zip"
    return "binary", "application/octet-stream"
