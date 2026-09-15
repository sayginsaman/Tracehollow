"""Stored connector credentials: set, remove, status and decryption for runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.models import User
from app.config import Settings
from app.connectors.base import CredentialSpec
from app.connectors.registry import get_connector
from app.db.base import utcnow
from app.integrations import crypto
from app.integrations.models import IntegrationCredential

logger = logging.getLogger(__name__)

MAX_CREDENTIAL_LENGTH = 4096


@dataclass(frozen=True)
class CredentialStatus:
    spec: CredentialSpec
    configured: bool
    updated_at: datetime | None
    last_used_at: datetime | None
    last_result: str | None
    usable: bool


def _spec(connector_id: str, name: str) -> CredentialSpec:
    connector = get_connector(connector_id)
    spec = (
        next((item for item in connector.descriptor.credentials if item.name == name), None)
        if connector
        else None
    )
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="credential_not_supported")
    return spec


def statuses(db: Session, settings: Settings, connector_id: str) -> list[CredentialStatus]:
    connector = get_connector(connector_id)
    if connector is None:
        return []
    rows = {
        row.name: row
        for row in db.scalars(
            select(IntegrationCredential).where(IntegrationCredential.connector_id == connector_id)
        )
    }
    current_key: str | None
    try:
        current_key = crypto.key_id_for(bytes.fromhex(_key_hex(settings)))
    except crypto.CredentialStoreUnavailableError:
        current_key = None
    result = []
    for spec in connector.descriptor.credentials:
        row = rows.get(spec.name)
        result.append(
            CredentialStatus(
                spec=spec,
                configured=row is not None,
                updated_at=row.updated_at if row else None,
                last_used_at=row.last_used_at if row else None,
                last_result=row.last_result if row else None,
                usable=row is not None and current_key is not None and row.key_id == current_key,
            )
        )
    return result


def _key_hex(settings: Settings) -> str:
    if settings.credential_encryption_key is None:
        raise crypto.CredentialStoreUnavailableError("no credential encryption key")
    return settings.credential_encryption_key.get_secret_value()


def set_credential(
    db: Session, settings: Settings, connector_id: str, name: str, value: str, user: User
) -> None:
    _spec(connector_id, name)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > MAX_CREDENTIAL_LENGTH or any(ch in cleaned for ch in "\r\n\0"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_credential",
                "message": "Enter a single-line value of at most 4096 characters.",
            },
        )
    try:
        sealed = crypto.seal(settings, connector_id, name, cleaned)
    except crypto.CredentialStoreUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="credential_store_unavailable"
        ) from None
    now = utcnow()
    db.execute(
        insert(IntegrationCredential)
        .values(
            connector_id=connector_id,
            name=name,
            ciphertext=sealed.ciphertext,
            nonce=sealed.nonce,
            key_id=sealed.key_id,
            updated_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["connector_id", "name"],
            set_={
                "ciphertext": sealed.ciphertext,
                "nonce": sealed.nonce,
                "key_id": sealed.key_id,
                "updated_by_user_id": user.id,
                "updated_at": now,
                "last_used_at": None,
                "last_result": None,
            },
        )
    )
    logger.info("integration_credential_set", extra={"connector": connector_id, "credential": name})


def delete_credential(db: Session, connector_id: str, name: str) -> None:
    _spec(connector_id, name)
    row = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.connector_id == connector_id, IntegrationCredential.name == name
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="credential_not_configured")
    db.delete(row)
    logger.info(
        "integration_credential_deleted", extra={"connector": connector_id, "credential": name}
    )


def read_plaintext(db: Session, settings: Settings, connector_id: str, name: str) -> str | None:
    """Decrypt for use by a run. ``None`` when not configured; raises on key problems."""
    row = db.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.connector_id == connector_id, IntegrationCredential.name == name
        )
    )
    if row is None:
        return None
    return crypto.open_sealed(settings, connector_id, name, row.ciphertext, row.nonce, row.key_id)


def record_result(db: Session, connector_id: str, name: str, result: str) -> None:
    db.execute(
        update(IntegrationCredential)
        .where(
            IntegrationCredential.connector_id == connector_id, IntegrationCredential.name == name
        )
        .values(last_used_at=utcnow(), last_result=result[:32])
    )
