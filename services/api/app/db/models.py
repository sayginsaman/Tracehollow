"""Import every model module so Alembic sees the complete metadata."""

from app.ai.models import (
    AiCitation,
    AiConversation,
    AiMessage,
    AiProviderStatus,
    AiRun,
    ChunkEmbedding,
    DocumentChunk,
    EmbeddingProfile,
    EvidenceIndexState,
)
from app.auth.models import User, UserSession
from app.cases.models import Case, CaseDeletion, CaseMember, Note
from app.db.base import Base
from app.dispatch.models import DispatchOutbox
from app.entities.models import (
    AnalystDecision,
    Entity,
    EntityEvidence,
    EntityIdentifier,
    Observation,
    Relationship,
    RelationshipEvidence,
)
from app.evidence.models import EvidenceObject
from app.integrations.models import IntegrationCredential, SourcePacing, SourceSlot
from app.queries.models import ConnectorRun, QueryRun, SavedQuery
from app.system.models import WorkerCheck

__all__ = [
    "AiCitation",
    "AiConversation",
    "AiMessage",
    "AiProviderStatus",
    "AiRun",
    "AnalystDecision",
    "Base",
    "Case",
    "CaseDeletion",
    "CaseMember",
    "ChunkEmbedding",
    "ConnectorRun",
    "DispatchOutbox",
    "DocumentChunk",
    "EmbeddingProfile",
    "Entity",
    "EntityEvidence",
    "EntityIdentifier",
    "EvidenceIndexState",
    "EvidenceObject",
    "IntegrationCredential",
    "Note",
    "Observation",
    "QueryRun",
    "Relationship",
    "RelationshipEvidence",
    "SavedQuery",
    "SourcePacing",
    "SourceSlot",
    "User",
    "UserSession",
    "WorkerCheck",
]
