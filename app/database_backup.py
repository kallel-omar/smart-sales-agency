"""Safe metadata helpers for PostgreSQL logical backup artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKUP_MANIFEST_FORMAT = "smart-sales-agency.postgresql-logical-backup.v1"


class BackupManifestError(ValueError):
    """Raised when backup artifact metadata cannot be verified safely."""


@dataclass(frozen=True)
class BackupManifest:
    format: str
    created_at: datetime
    application_revision: str
    archive_filename: str
    archive_size_bytes: int
    sha256: str

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "created_at": self.created_at.isoformat(),
            "application_revision": self.application_revision,
            "archive_filename": self.archive_filename,
            "archive_size_bytes": self.archive_size_bytes,
            "sha256": self.sha256,
        }


def create_backup_manifest(
    archive_path: str | Path,
    *,
    application_revision: str,
) -> BackupManifest:
    """Return safe integrity metadata for a completed backup archive."""

    archive = Path(archive_path)
    if not archive.is_file():
        raise BackupManifestError("Backup archive does not exist")
    size = archive.stat().st_size
    if size <= 0:
        raise BackupManifestError("Backup archive is empty")

    return BackupManifest(
        format=BACKUP_MANIFEST_FORMAT,
        created_at=datetime.now(UTC),
        application_revision=application_revision,
        archive_filename=archive.name,
        archive_size_bytes=size,
        sha256=sha256_file(archive),
    )


def write_backup_manifest(manifest: BackupManifest, manifest_path: str | Path) -> None:
    Path(manifest_path).write_text(
        json.dumps(manifest.to_safe_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_backup_manifest(
    manifest: BackupManifest,
    *,
    backup_directory: str | Path,
) -> None:
    """Verify archive presence, size, and checksum against safe metadata."""

    archive = Path(backup_directory) / manifest.archive_filename
    if not archive.is_file():
        raise BackupManifestError("Backup archive does not exist")
    if archive.stat().st_size != manifest.archive_size_bytes:
        raise BackupManifestError("Backup archive size does not match manifest")
    if sha256_file(archive) != manifest.sha256:
        raise BackupManifestError("Backup archive checksum does not match manifest")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
