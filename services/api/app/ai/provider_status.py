"""Provider connectivity checks, run by the AI worker and stored in PostgreSQL.

Only the local provider is contacted. A configured cloud provider is recorded as configured but
not checked: Tracehollow does not call a cloud provider until a case explicitly sends a request.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.ai.models import AiProviderStatus, ProcessingLocation
from app.ai.policy import ProviderSet, local_location
from app.config import Settings
from app.db.base import utcnow
from app.db.session import session_scope


def _upsert(db: Session, **values: object) -> None:
    statement = insert(AiProviderStatus).values(checked_at=utcnow(), **values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["provider"],
            set_={
                key: statement.excluded[key]
                for key in (
                    "location",
                    "configured",
                    "reachable",
                    "generation_model",
                    "generation_model_available",
                    "embedding_model",
                    "embedding_model_available",
                    "embedding_model_digest",
                    "error_code",
                    "checked_at",
                )
            },
        )
    )


def check_providers(
    session_factory: sessionmaker[Session], settings: Settings, providers: ProviderSet
) -> dict[str, object]:
    local = providers.local_generation
    inventory = providers.embeddings.inventory()
    generation_available = local.model in inventory.models if inventory.reachable else None
    embedding_available = (
        providers.embeddings.model in inventory.models if inventory.reachable else None
    )
    error = inventory.error_code
    if inventory.reachable and not (generation_available and embedding_available):
        error = "model_not_found"
    with session_scope(session_factory) as db:
        _upsert(
            db,
            provider=local.name,
            location=local_location(settings).value,
            configured=True,
            reachable=inventory.reachable,
            generation_model=local.model,
            generation_model_available=generation_available,
            embedding_model=providers.embeddings.model,
            embedding_model_available=embedding_available,
            embedding_model_digest=inventory.models.get(providers.embeddings.model),
            error_code=error,
        )
        if settings.ai_cloud_provider != "none":
            _upsert(
                db,
                provider=settings.ai_cloud_provider,
                location=ProcessingLocation.CLOUD.value,
                configured=settings.ai_cloud_configured,
                reachable=None,
                generation_model=settings.ai_cloud_model,
                generation_model_available=None,
                embedding_model=None,
                embedding_model_available=None,
                embedding_model_digest=None,
                error_code=None if settings.ai_cloud_configured else "cloud_api_key_missing",
            )
    return {"reachable": inventory.reachable, "error_code": error}
