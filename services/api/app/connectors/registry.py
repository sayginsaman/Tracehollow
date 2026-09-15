"""Registered connectors. Phase 1 ships only the synthetic fixture; no live integrations exist."""

from __future__ import annotations

from app.connectors.base import Connector
from app.connectors.fixture import FixtureConnector

_REGISTRY: dict[str, Connector] = {
    FixtureConnector.descriptor.connector_id: FixtureConnector(),
}


def get_connector(connector_id: str) -> Connector | None:
    return _REGISTRY.get(connector_id)


def all_connectors() -> list[Connector]:
    return list(_REGISTRY.values())
