"""Extract readable text and page metadata from untrusted HTML without executing anything.

Uses the standard library tokenizer. Scripts, styles, templates and embedded SVG/MathML are
skipped; block elements become line breaks; whitespace is collapsed. The output is plain text
stored as derived evidence next to the byte-exact original snapshot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "math", "iframe", "object"})
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }
)  # fmt: skip
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.I)
_PUBLISHED_KEYS = frozenset(
    {"article:published_time", "og:published_time", "datepublished", "dc.date", "date"}
)
MAX_LINKS = 200


@dataclass
class ExtractedPage:
    title: str | None = None
    description: str | None = None
    language: str | None = None
    canonical_url: str | None = None
    published: str | None = None
    text: str = ""
    links: list[str] = field(default_factory=list)


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.page = ExtractedPage()
        self._skip_depth = 0
        self._in_title = False
        self._title: list[str] = []
        self._parts: list[str] = []
        self._links: dict[str, None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if tag == "html" and values.get("lang"):
            self.page.language = values["lang"][:35]
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = values.get("name") or values.get("property") or values.get("itemprop") or ""
            key = key.lower()
            content = values.get("content", "").strip()
            if key in ("description", "og:description") and content and not self.page.description:
                self.page.description = content[:1000]
            if key in _PUBLISHED_KEYS and content and not self.page.published:
                self.page.published = content[:64]
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            href = values.get("href", "").strip()
            if href:
                self.page.canonical_url = urljoin(self.base_url, href)[:2048]
        elif tag == "a":
            href = values.get("href", "").strip()
            if href and len(self._links) < MAX_LINKS:
                absolute = urljoin(self.base_url, href)
                if urlsplit(absolute).scheme in ("http", "https"):
                    self._links[absolute[:2048]] = None
        if tag in _BLOCK:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title.append(data)
            return
        self._parts.append(data)

    def result(self) -> ExtractedPage:
        title = " ".join("".join(self._title).split())
        self.page.title = title[:300] or None
        lines = []
        for line in "".join(self._parts).split("\n"):
            collapsed = " ".join(line.split())
            if collapsed:
                lines.append(collapsed)
        self.page.text = "\n".join(lines)
        self.page.links = list(self._links)
        return self.page


def sniff_charset(content: bytes) -> str | None:
    match = _META_CHARSET.search(content[:4096])
    return match.group(1).decode("ascii").lower() if match else None


def extract(html: str, base_url: str) -> ExtractedPage:
    parser = _Extractor(base_url)
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        # The tokenizer tolerates almost anything; keep whatever was read before a hard error.
        pass
    return parser.result()
