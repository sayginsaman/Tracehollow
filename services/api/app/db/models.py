"""Import every model module so Alembic sees the complete metadata."""

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
from app.queries.models import ConnectorRun, QueryRun, SavedQuery
from app.system.models import WorkerCheck

__all__ = [
    "AnalystDecision",
    "Base",
    "Case",
    "CaseDeletion",
    "CaseMember",
    "ConnectorRun",
    "DispatchOutbox",
    "Entity",
    "EntityEvidence",
    "EntityIdentifier",
    "EvidenceObject",
    "Note",
    "Observation",
    "QueryRun",
    "Relationship",
    "RelationshipEvidence",
    "SavedQuery",
    "User",
    "UserSession",
    "WorkerCheck",
]
