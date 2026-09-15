"""Passive Subfinder sources and the provider host names each one is allowed to contact.

Traced from the Subfinder v2.16.0 source (``pkg/subscraping/sources/<name>``, 2026-09-15). This
module is the single allowlist shared by the connector (source choices), the sandbox runner
(input validation) and the egress gateway (CONNECT destinations). It uses only the standard
library because the runner image has no application dependencies.

Notes from the trace:

* ``crtsh`` first opens a PostgreSQL connection to ``crt.sh:5432`` (not HTTP, so it ignores the
  proxy) and falls back to ``https://crt.sh/?q=...`` when that fails. In the sandbox the database
  connection cannot resolve or route, so only the HTTPS API is used.
* Subfinder's HTTP client sets ``InsecureSkipVerify``; the gateway verifies provider certificates
  itself.
* ``digitorus`` treats an HTTP 404 page as a result page.
"""

from __future__ import annotations

from typing import TypedDict


class SourceSpec(TypedDict):
    label: str
    key: str | None
    hosts: tuple[str, ...]


SUBFINDER_SOURCES: dict[str, SourceSpec] = {
    "crtsh": {
        "label": "crt.sh certificate transparency search",
        "key": None,
        "hosts": ("crt.sh",),
    },
    "digitorus": {
        "label": "Digitorus certificate transparency",
        "key": None,
        "hosts": ("certificatedetails.com",),
    },
    "anubis": {
        "label": "Anubis subdomain database (anubisdb.com)",
        "key": None,
        "hosts": ("anubisdb.com",),
    },
    "hackertarget": {
        "label": "HackerTarget host search (free tier is limited)",
        "key": None,
        "hosts": ("api.hackertarget.com",),
    },
    "rapiddns": {"label": "RapidDNS", "key": None, "hosts": ("rapiddns.io",)},
    "certspotter": {
        "label": "Cert Spotter (SSLMate) - API key",
        "key": "certspotter",
        "hosts": ("api.certspotter.com",),
    },
    "virustotal": {
        "label": "VirusTotal - API key",
        "key": "virustotal",
        "hosts": ("www.virustotal.com",),
    },
    "alienvault": {
        "label": "AlienVault OTX passive DNS - API key",
        "key": "alienvault",
        "hosts": ("otx.alienvault.com",),
    },
    "securitytrails": {
        "label": "SecurityTrails - API key",
        "key": "securitytrails",
        "hosts": ("api.securitytrails.com",),
    },
}

PROVIDER_PORT = 443


def provider_hosts() -> frozenset[str]:
    return frozenset(host for spec in SUBFINDER_SOURCES.values() for host in spec["hosts"])
