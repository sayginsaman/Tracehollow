"""Registered connectors. Only these can be selected in saved queries."""

from __future__ import annotations

from app.connectors.base import Connector
from app.connectors.fixture import FixtureConnector
from app.connectors.github import GitHubAccountConnector
from app.connectors.instagram import InstagramAccountConnector
from app.connectors.rss import RssFeedConnector
from app.connectors.sherlock import SherlockUsernameConnector
from app.connectors.subfinder import SubfinderDomainConnector
from app.connectors.telegram import TelegramPublicChannelConnector
from app.connectors.web import PublicWebPageConnector
from app.connectors.youtube import YouTubeDataApiConnector

_REGISTRY: dict[str, Connector] = {
    connector.descriptor.connector_id: connector
    for connector in (
        FixtureConnector(),
        PublicWebPageConnector(),
        RssFeedConnector(),
        GitHubAccountConnector(),
        SherlockUsernameConnector(),
        SubfinderDomainConnector(),
        InstagramAccountConnector(),
        TelegramPublicChannelConnector(),
        YouTubeDataApiConnector(),
    )
}


def get_connector(connector_id: str) -> Connector | None:
    return _REGISTRY.get(connector_id)


def all_connectors() -> list[Connector]:
    return list(_REGISTRY.values())
