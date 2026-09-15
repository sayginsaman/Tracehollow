"""Parse RSS 2.0, RSS 1.0 (RDF) and Atom 1.0 feeds from untrusted bytes.

XML is parsed with defusedxml: entity declarations and external references are rejected, so
entity-expansion and external-entity attacks fail with a parse error instead of being processed.
Entry HTML is reduced to text; nothing from the feed is rendered or executed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from app.connectors.htmltext import extract

ATOM = "{http://www.w3.org/2005/Atom}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
MAX_SUMMARY_CHARS = 4000


class FeedParseError(ValueError):
    pass


@dataclass
class FeedEntry:
    entry_id: str
    id_source: str
    title: str | None
    link: str | None
    published: datetime | None
    published_original: str | None
    updated: datetime | None
    author: str | None
    summary: str
    categories: list[str] = field(default_factory=list)


@dataclass
class ParsedFeed:
    format: str
    title: str | None
    site_link: str | None
    feed_id: str | None
    updated: datetime | None
    next_url: str | None
    entries: list[FeedEntry]


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # A timestamp without an offset cannot be placed on the UTC timeline reliably.
        return None
    return parsed.astimezone(UTC)


def _text(element: Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _html_text(value: str | None, base: str) -> str:
    if not value:
        return ""
    if "<" not in value:
        return " ".join(value.split())[:MAX_SUMMARY_CHARS]
    return extract(value, base).text[:MAX_SUMMARY_CHARS]


def _entry_id(
    explicit: str | None, source: str, link: str | None, title: str | None, date: str | None
) -> tuple[str, str]:
    if explicit:
        return explicit[:512], source
    if link:
        return link[:512], "link"
    digest = hashlib.sha256(f"{title or ''}\x1f{date or ''}".encode()).hexdigest()
    return f"sha256:{digest[:32]}", "content_hash"


def _atom_link(element: Element, rel: str, base: str) -> str | None:
    for link in element.findall(f"{ATOM}link"):
        if (link.get("rel") or "alternate") == rel and link.get("href"):
            return urljoin(base, link.get("href", ""))
    return None


def parse_feed(content: bytes, base_url: str) -> ParsedFeed:
    try:
        root = SafeET.fromstring(content, forbid_dtd=False)
    except (ParseError, DefusedXmlException) as exc:
        raise FeedParseError(f"The feed is not well-formed XML ({type(exc).__name__}).") from exc
    if root.tag == "rss":
        return _parse_rss2(root, base_url)
    if root.tag == f"{ATOM}feed":
        return _parse_atom(root, base_url)
    if root.tag == f"{RDF}RDF":
        return _parse_rss1(root, base_url)
    raise FeedParseError("The document is XML but not an RSS or Atom feed.")


def _parse_rss2(root: Element, base: str) -> ParsedFeed:
    channel = root.find("channel")
    if channel is None:
        raise FeedParseError("The RSS document has no channel.")
    site = _text(channel.find("link"))
    entries = []
    for item in channel.findall("item"):
        guid = _text(item.find("guid"))
        link = _text(item.find("link"))
        title = _text(item.find("title"))
        date = _text(item.find("pubDate")) or _text(item.find(f"{DC}date"))
        entry_id, source = _entry_id(guid, "guid", link, title, date)
        summary = _text(item.find(f"{CONTENT}encoded")) or _text(item.find("description"))
        entries.append(
            FeedEntry(
                entry_id=entry_id,
                id_source=source,
                title=title,
                link=urljoin(base, link) if link else None,
                published=parse_date(date),
                published_original=date,
                updated=None,
                author=_text(item.find("author")) or _text(item.find(f"{DC}creator")),
                summary=_html_text(summary, base),
                categories=[c for c in (_text(x) for x in item.findall("category")) if c][:20],
            )
        )
    return ParsedFeed(
        format="rss2",
        title=_text(channel.find("title")),
        site_link=urljoin(base, site) if site else None,
        feed_id=None,
        updated=parse_date(_text(channel.find("lastBuildDate"))),
        next_url=_atom_link(channel, "next", base),
        entries=entries,
    )


def _parse_rss1(root: Element, base: str) -> ParsedFeed:
    channel = root.find(f"{RSS1}channel")
    entries = []
    for item in root.findall(f"{RSS1}item"):
        link = _text(item.find(f"{RSS1}link"))
        title = _text(item.find(f"{RSS1}title"))
        date = _text(item.find(f"{DC}date"))
        entry_id, source = _entry_id(item.get(f"{RDF}about"), "rdf_about", link, title, date)
        entries.append(
            FeedEntry(
                entry_id=entry_id,
                id_source=source,
                title=title,
                link=urljoin(base, link) if link else None,
                published=parse_date(date),
                published_original=date,
                updated=None,
                author=_text(item.find(f"{DC}creator")),
                summary=_html_text(_text(item.find(f"{RSS1}description")), base),
            )
        )
    return ParsedFeed(
        format="rss1",
        title=_text(channel.find(f"{RSS1}title")) if channel is not None else None,
        site_link=_text(channel.find(f"{RSS1}link")) if channel is not None else None,
        feed_id=None,
        updated=None,
        next_url=None,
        entries=entries,
    )


def _parse_atom(root: Element, base: str) -> ParsedFeed:
    entries = []
    for item in root.findall(f"{ATOM}entry"):
        link = _atom_link(item, "alternate", base)
        title = _text(item.find(f"{ATOM}title"))
        published_raw = _text(item.find(f"{ATOM}published"))
        updated_raw = _text(item.find(f"{ATOM}updated"))
        entry_id, source = _entry_id(
            _text(item.find(f"{ATOM}id")), "atom_id", link, title, published_raw or updated_raw
        )
        summary = _text(item.find(f"{ATOM}content")) or _text(item.find(f"{ATOM}summary"))
        author = item.find(f"{ATOM}author")
        entries.append(
            FeedEntry(
                entry_id=entry_id,
                id_source=source,
                title=title,
                link=link,
                published=parse_date(published_raw),
                published_original=published_raw,
                updated=parse_date(updated_raw),
                author=_text(author.find(f"{ATOM}name")) if author is not None else None,
                summary=_html_text(summary, base),
                categories=[
                    c.get("term", "") for c in item.findall(f"{ATOM}category") if c.get("term")
                ][:20],
            )
        )
    return ParsedFeed(
        format="atom",
        title=_text(root.find(f"{ATOM}title")),
        site_link=_atom_link(root, "alternate", base),
        feed_id=_text(root.find(f"{ATOM}id")),
        updated=parse_date(_text(root.find(f"{ATOM}updated"))),
        next_url=_atom_link(root, "next", base),
        entries=entries,
    )
