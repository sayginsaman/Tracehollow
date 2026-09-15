"""Filesystem storage for original evidence bytes.

Layout under the evidence root (the persistent ``evidence-data`` volume)::

    tmp/<random>.part                   staged writes (fsynced before promotion)
    cases/<case-uuid>/evidence/<uuid>   promoted originals, named only by server-generated UUIDs
    quarantine/<timestamp>-<name>       orphaned files moved aside by reconciliation

User-supplied filenames never influence paths. Promotion refuses to replace an existing file,
so stored evidence is never silently overwritten. Metadata lives in PostgreSQL; see
``docs/operations/evidence-storage.md`` for crash-recovery semantics.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

_CHUNK = 1024 * 1024


class StorageError(RuntimeError):
    pass


class IntegrityError(StorageError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StagedFile:
    path: Path
    sha256: str
    size_bytes: int


class EvidenceStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # -- paths -----------------------------------------------------------------------------

    @staticmethod
    def key_for(case_id: uuid.UUID, evidence_id: uuid.UUID) -> str:
        return f"cases/{case_id}/evidence/{evidence_id}"

    def path_for_key(self, key: str) -> Path:
        parts = key.split("/")
        if (
            len(parts) != 4
            or parts[0] != "cases"
            or parts[2] != "evidence"
            or not _is_uuid(parts[1])
            or not _is_uuid(parts[3])
        ):
            raise StorageError("invalid_storage_key")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("invalid_storage_key")
        return path

    def case_dir(self, case_id: uuid.UUID) -> Path:
        return self.root / "cases" / str(case_id)

    # -- writes ----------------------------------------------------------------------------

    def stage(self, content: bytes) -> StagedFile:
        tmp_dir = self.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path = tmp_dir / f"{secrets.token_hex(16)}.part"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StagedFile(path, hashlib.sha256(content).hexdigest(), len(content))

    def promote(self, staged: StagedFile, key: str) -> Path:
        final = self.path_for_key(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise StorageError("storage_key_exists")
        os.replace(staged.path, final)
        _fsync_dir(final.parent)
        return final

    def discard(self, path: Path | None) -> None:
        if path is not None:
            path.unlink(missing_ok=True)

    def store(self, key: str, content: bytes) -> StagedFile:
        """Stage and promote in one step. Callers commit metadata afterwards and must call
        ``remove_key`` if the commit fails."""
        staged = self.stage(content)
        try:
            self.promote(staged, key)
        except BaseException:
            self.discard(staged.path)
            raise
        return staged

    def remove_key(self, key: str) -> None:
        self.path_for_key(key).unlink(missing_ok=True)

    # -- reads -----------------------------------------------------------------------------

    def read_verified(self, key: str, expected_sha256: str, expected_size: int) -> bytes:
        path = self.path_for_key(key)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            raise IntegrityError("evidence_file_missing") from None
        if len(content) != expected_size:
            raise IntegrityError("evidence_size_mismatch")
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise IntegrityError("evidence_hash_mismatch")
        return content

    def hash_file(self, key: str) -> tuple[str, int] | None:
        path = self.path_for_key(key)
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    # -- deletion --------------------------------------------------------------------------

    def delete_case_files(self, case_id: uuid.UUID) -> int:
        """Remove every stored file for a case. Idempotent; raises on I/O failure."""
        directory = self.case_dir(case_id)
        if not directory.exists():
            return 0
        count = sum(1 for item in directory.rglob("*") if item.is_file())
        shutil.rmtree(directory)
        if directory.exists():
            raise StorageError("case_directory_still_present")
        return count

    def case_files_exist(self, case_id: uuid.UUID) -> bool:
        return self.case_dir(case_id).exists()


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
