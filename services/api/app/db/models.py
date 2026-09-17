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
from app.audit.models import AuditEvent
from app.auth.models import User, UserSession
from app.budgets.models import BudgetLedger, BudgetReservation, CaseBudget
from app.cases.models import Case, CaseDeletion, CaseMember, Note
from app.changes.models import ChangeEvent, ChangeSet
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
from app.imports.models import ProcessingJob
from app.integrations.models import IntegrationCredential, SourcePacing, SourceSlot
from app.monitoring.models import Monitor, MonitorOccurrence
from app.notifications.models import (
    MonitorSubscription,
    Notification,
    NotificationDelivery,
    NotificationDestination,
)
from app.queries.models import ConnectorRun, QueryRun, SavedQuery
from app.system.models import WorkerCheck

__all__ = [
    "AiCitation",
    "AiConversation",
    "AiMessage",
    "AiProviderStatus",
    "AiRun",
    "AnalystDecision",
    "AuditEvent",
    "Base",
    "BudgetLedger",
    "BudgetReservation",
    "Case",
    "CaseBudget",
    "CaseDeletion",
    "CaseMember",
    "ChangeEvent",
    "ChangeSet",
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
    "Monitor",
    "MonitorOccurrence",
    "MonitorSubscription",
    "Note",
    "Notification",
    "NotificationDelivery",
    "NotificationDestination",
    "Observation",
    "ProcessingJob",
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
