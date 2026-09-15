"""Text normalization for retrieval: search folding, exact identifiers and quote location.

Search folding is deliberately lossy (it improves recall for queries typed without Turkish
characters). It is only used for matching; stored chunks and citations keep the original text.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass

from app.auth.security import normalize_username
from app.entities.normalize import IdentifierError, normalize_domain, normalize_email, normalize_url

_TURKISH_I = str.maketrans({"İ": "i", "I": "i", "ı": "i"})
_WHITESPACE = re.compile(r"\s+")

_URL = re.compile(r"https?://[^\s<>\"'`\]\[(){}]+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
_DOMAIN = re.compile(r"(?<![\w@.-])(?:[^\W_](?:[\w-]{0,61}[^\W_])?\.)+[^\W\d_]{2,63}(?![\w-])")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])", re.IGNORECASE)
_HASH = re.compile(
    r"(?<![0-9a-f])(?:[0-9a-f]{128}|[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})(?![0-9a-f])"
)
_HANDLE = re.compile(r"(?<![\w@])@([\w.]{2,64})", re.UNICODE)
_UNDERSCORED = re.compile(r"(?<![\w@.-])[^\W\d_][\w.-]*_[\w.-]+(?![\w-])", re.UNICODE)

# Tokens shaped like domains that are almost always filenames or abbreviations.
_NOT_TLDS = frozenset(
    {
        "json",
        "txt",
        "csv",
        "html",
        "htm",
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "md",
        "xml",
        "zip",
        "exe",
        "js",
        "py",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "log",
        "eml",
        "svg",
        "tar",
        "gz",
    }
)
MAX_IDENTIFIERS = 64


def fold_for_search(value: str) -> str:
    """Case, accent and Turkish dotted/dotless ``i`` folding for full-text matching."""
    folded = unicodedata.normalize("NFKC", value).translate(_TURKISH_I).lower()
    decomposed = unicodedata.normalize("NFD", folded)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", stripped)).strip()


def extract_identifiers(text: str) -> list[str]:
    """Normalized exact identifiers (typed keys such as ``domain:ornek.example``) in ``text``."""
    found: dict[str, None] = {}

    def add(key: str) -> None:
        if len(found) < MAX_IDENTIFIERS and len(key) <= 512:
            found.setdefault(key, None)

    for match in _URL.finditer(text):
        raw = match.group(0).rstrip(".,;:!?")
        try:
            url = normalize_url(raw)
        except IdentifierError:
            continue
        add(f"url:{url}")
        host = url.split("://", 1)[1].split("/", 1)[0].rsplit("@", 1)[-1]
        if not host.startswith("["):
            host = host.split(":", 1)[0]
            with contextlib.suppress(IdentifierError):
                add(f"domain:{normalize_domain(host)}")
    for match in _EMAIL.finditer(text):
        try:
            email = normalize_email(match.group(0).rstrip("."))
        except IdentifierError:
            continue
        add(f"email:{email}")
        with contextlib.suppress(IdentifierError):
            add(f"domain:{normalize_domain(email.rsplit('@', 1)[1])}")
    for match in _DOMAIN.finditer(_URL.sub(" ", _EMAIL.sub(" ", text))):
        candidate = match.group(0)
        if candidate.rsplit(".", 1)[-1].lower() in _NOT_TLDS:
            continue
        try:
            add(f"domain:{normalize_domain(candidate)}")
        except IdentifierError:
            continue
    for pattern in (_IPV4, _IPV6):
        for match in pattern.finditer(text):
            try:
                add(f"ip:{ipaddress.ip_address(match.group(0)).compressed}")
            except ValueError:
                continue
    for match in _HASH.finditer(text.lower()):
        add(f"hash:{match.group(0)}")
    for match in _HANDLE.finditer(text):
        add(f"username:{normalize_username(match.group(1).rstrip('.'))}")
    for match in _UNDERSCORED.finditer(_URL.sub(" ", _EMAIL.sub(" ", text))):
        add(f"username:{normalize_username(match.group(0).rstrip('.'))}")
    return list(found)


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    start: int
    end: int


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """Fold ``text`` for quote matching and map every folded character to its source index."""
    out: list[str] = []
    index_map: list[int] = []
    previous_space = True
    for position, char in enumerate(text):
        folded = fold_for_search(char) if not char.isspace() else " "
        if folded == " " or folded == "":
            if folded == " " and not previous_space:
                out.append(" ")
                index_map.append(position)
                previous_space = True
            continue
        for piece in folded:
            out.append(piece)
            index_map.append(position)
        previous_space = False
    return "".join(out), index_map


def locate_quote(haystack: str, quote: str) -> QuoteMatch | None:
    """Find ``quote`` in ``haystack`` ignoring case, accents and whitespace differences.

    Returns the matching range in ``haystack``'s own indices, or ``None`` when the quote is
    not present. Quotes shorter than four folded characters are not accepted as evidence.
    """
    needle, _ = _normalized_with_map(quote)
    needle = needle.strip()
    if len(needle) < 4:
        return None
    folded, index_map = _normalized_with_map(haystack)
    position = folded.find(needle)
    if position < 0:
        return None
    start = index_map[position]
    end = index_map[position + len(needle) - 1] + 1
    return QuoteMatch(start, end)
