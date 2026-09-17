"""Redaction applied to every piece of case text before it is escaped into a report.

Two kinds of replacement:

* analyst redactions: literal terms and all identifier values of selected types, replaced with
  ``[redacted]`` (case-insensitive, longest first);
* credential scrubbing, always on: strings shaped like API keys, bearer tokens, bot tokens and
  secret-bearing query parameters are replaced with ``[credential removed]``. Reports never read
  stored credentials or sessions; this catches values a source may have echoed into evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REDACTED = "[redacted]"
CREDENTIAL = "[credential removed]"

_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bEAA[A-Za-z0-9]{30,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:access_token|api[_-]?key|key|token|auth|password|secret|sig|signature)=)[^&#\s\"'<>]+"
)
_BOT_PATH = re.compile(r"(/bot)\d{6,12}:[A-Za-z0-9_-]{20,}")


@dataclass
class Redactor:
    terms: list[str] = field(default_factory=list)
    redactions: int = 0
    credentials: int = 0
    _pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        unique = sorted(
            {term for term in self.terms if len(term.strip()) >= 2}, key=len, reverse=True
        )
        if unique:
            self._pattern = re.compile("|".join(re.escape(term) for term in unique), re.IGNORECASE)

    def __call__(self, value: object) -> str:
        if value is None:
            return ""
        text = str(value)
        text, count = _BOT_PATH.subn(rf"\1{CREDENTIAL}", text)
        self.credentials += count
        text, count = _SECRET_QUERY.subn(rf"\1{CREDENTIAL}", text)
        self.credentials += count
        for pattern in _CREDENTIAL_PATTERNS:
            text, count = pattern.subn(CREDENTIAL, text)
            self.credentials += count
        if self._pattern is not None:
            text, count = self._pattern.subn(REDACTED, text)
            self.redactions += count
        return text
