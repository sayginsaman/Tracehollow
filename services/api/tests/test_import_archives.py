"""Hostile archives and filenames (PRD Phase 4 acceptance criterion 5)."""

from __future__ import annotations

import io
import stat
import zipfile

import pytest

from app.imports.archives import (
    ArchiveLimits,
    ArchiveRejectedError,
    ArchiveReport,
    iter_members,
    normalize_member_name,
    sniff_content_type,
)

LIMITS = ArchiveLimits(
    max_members=20, max_member_bytes=4 * 1024 * 1024, max_total_bytes=8 * 1024 * 1024, max_ratio=50
)


def _zip(entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue()


def _mark_encrypted(content: bytes, name: str) -> bytes:
    """Set the "encrypted" general-purpose flag for ``name`` in the local and central headers.

    zipfile clears this flag when writing, so a test archive has to be patched to contain it.
    """
    data = bytearray(content)
    encoded = name.encode()
    for signature, flag_offset, name_length_offset, name_offset in (
        (b"PK\x03\x04", 6, 26, 30),
        (b"PK\x01\x02", 8, 28, 46),
    ):
        start = 0
        while (position := data.find(signature, start)) != -1:
            length = int.from_bytes(
                data[position + name_length_offset : position + name_length_offset + 2], "little"
            )
            if bytes(data[position + name_offset : position + name_offset + length]) == encoded:
                flags = int.from_bytes(
                    data[position + flag_offset : position + flag_offset + 2], "little"
                )
                data[position + flag_offset : position + flag_offset + 2] = (flags | 1).to_bytes(
                    2, "little"
                )
            start = position + 4
    return bytes(data)


def _read(content: bytes, limits: ArchiveLimits = LIMITS) -> tuple[list[str], ArchiveReport]:
    report = ArchiveReport()
    names = [member.name for member in iter_members(content, limits, report)]
    return names, report


@pytest.mark.parametrize(
    "raw",
    [
        "../evil.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "a/../../b.txt",
        "..\\..\\x.txt",
        "a\x00b",
    ],
)
def test_names_that_escape_the_root_are_unsafe(raw: str) -> None:
    assert normalize_member_name(raw) is None


def test_ordinary_and_turkish_names_are_kept_in_nfc() -> None:
    decomposed = "S\u0327u\u0308kru\u0308.jpg"  # Şükrü with combining marks
    assert normalize_member_name(f"medya/./{decomposed}") == "medya/\u015e\u00fckr\u00fc.jpg"


def test_traversal_symlink_and_encrypted_members_are_skipped_and_reported() -> None:
    link = zipfile.ZipInfo("shortcut")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    content = _mark_encrypted(
        _zip(
            [
                ("_chat.txt", b"[31.12.21, 10:00:00] Ali: merhaba\n"),
                ("../../outside.txt", b"escape"),
                (link, b"/etc/passwd"),
                ("locked.txt", b"ciphertext"),
                ("_CHAT.TXT", b"duplicate differing only in case"),
            ]
        ),
        "locked.txt",
    )
    names, report = _read(content)
    assert names == ["_chat.txt"]
    assert {(item.name, item.reason) for item in report.skipped} == {
        ("../../outside.txt", "unsafe_path"),
        ("shortcut", "symbolic_link"),
        ("locked.txt", "encrypted_member"),
        ("_CHAT.TXT", "duplicate_name"),
    }


def test_a_decompression_bomb_member_is_skipped() -> None:
    bomb = b"\x00" * (3 * 1024 * 1024)  # compresses far beyond the 50:1 limit
    names, report = _read(_zip([("zeros.bin", bomb), ("note.txt", b"ok")]))
    assert names == ["note.txt"]
    assert report.skipped[0].reason == "expansion_ratio_exceeded"


def test_too_many_members_rejects_the_archive() -> None:
    content = _zip([(f"f{i}.txt", b"x") for i in range(21)])
    with pytest.raises(ArchiveRejectedError) as caught:
        _read(content)
    assert caught.value.code == "too_many_members"


def test_total_expanded_size_is_bounded() -> None:
    limits = ArchiveLimits(
        max_members=20, max_member_bytes=4 * 1024 * 1024, max_total_bytes=1024, max_ratio=10_000
    )
    content = _zip([("a.bin", bytes(range(256)) * 3), ("b.bin", bytes(range(256)) * 3)])
    with pytest.raises(ArchiveRejectedError) as caught:
        _read(content, limits)
    assert caught.value.code == "archive_too_large"


def test_oversized_member_is_skipped_without_reading() -> None:
    limits = ArchiveLimits(
        max_members=20, max_member_bytes=100, max_total_bytes=10_000, max_ratio=10_000
    )
    names, report = _read(_zip([("big.bin", bytes(range(256))), ("small.txt", b"ok")]), limits)
    assert names == ["small.txt"]
    assert report.skipped[0].reason == "member_too_large"


def test_nested_archives_are_kept_as_files_not_expanded() -> None:
    inner = _zip([("inner.txt", b"never expanded")])
    report = ArchiveReport()
    members = list(iter_members(_zip([("inner.zip", inner)]), LIMITS, report))
    assert [member.name for member in members] == ["inner.zip"]
    assert sniff_content_type(members[0].data) == ("binary", "application/zip")


def test_something_that_is_not_a_zip_is_rejected() -> None:
    with pytest.raises(ArchiveRejectedError) as caught:
        _read(b"PK\x03\x04 this is not really an archive")
    assert caught.value.code == "invalid_archive"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"%PDF-1.7\n", ("pdf", "application/pdf")),
        (b"\xff\xd8\xff\xe0JFIF", ("binary", "image/jpeg")),
        (b"\x89PNG\r\n\x1a\n....", ("binary", "image/png")),
        (b"OggS\x00\x02", ("binary", "audio/ogg")),
        (b"<script>alert(1)</script>", ("binary", "application/octet-stream")),
        (b"<svg onload=alert(1)>", ("binary", "application/octet-stream")),
    ],
)
def test_content_type_comes_from_bytes_and_active_content_stays_opaque(
    data: bytes, expected: tuple[str, str]
) -> None:
    assert sniff_content_type(data) == expected
