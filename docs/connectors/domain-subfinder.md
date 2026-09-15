# Passive subdomain discovery with Subfinder (`domain.subfinder` 1.0.0)

Lists subdomains of a domain from passive third-party datasets using ProjectDiscovery Subfinder.
The domain's own servers are not contacted and names are not resolved.

| | |
| --- | --- |
| Mode | Third-party lookup: the selected sources learn which domain was looked up |
| Input | Domain name (IDNA accepted; no wildcards, no IP addresses) |
| Parameters | `sources` (default `crtsh`, `digitorus`); `max_results` (1-5000, default 500) |
| Sources | keyless: `crtsh`, `digitorus`, `anubis`, `hackertarget` (limited free tier), `rapiddns`; key-based: `certspotter`, `virustotal`, `alienvault`, `securitytrails` |
| Credentials | optional API keys per key-based source (Sources screen) |
| Engine | Subfinder v2.16.0 (MIT) binary in the collector image, SHA-256 verified against the release checksums at build time |
| Limits | 1 page, 300 s run timeout, 1 concurrent run, `max_results` in-scope names |
| Retries | 2 attempts for `unavailable` |
| Cache | none |
| Output schema | `tracehollow.domain.subdomains/v1` |
| Live verification | not performed |

## How it runs

`subfinder -d <domain> -s <sources> -oJ -cs -duc -nc -v -timeout <s> -max-time <min> -rsr <bytes>
-config <tmp> -pc <tmp>` as an argument list in a private temporary directory with a minimal
environment. `-duc` disables the update check; there is no `-active`/`-nW`, so names are never
resolved. Keys are written to a temporary provider configuration only for selected sources and
deleted after the run.

Compatibility check (2026-09-15): Subfinder exits successfully with no output when every source
fails, which is indistinguishable from "no subdomains". The adapter reads its verbose messages
("Selected source(s)", "Encountered an error with source", "Cannot use the … source because there
was no API key") to know which sources answered.

## Outcomes

| Situation | Outcome |
| --- | --- |
| Names found, every selected source answered | `findings` |
| No names, every selected source answered | `no_findings` |
| Names found, some sources failed or were skipped, the result or time limit was reached | `partial` |
| No names and no source answered: all skipped for missing keys | `authentication_required` |
| No names and no source answered: all errors were rate limits or quota | `rate_limited` |
| No names and no source answered otherwise | `unavailable` |
| Engine error or not installed | `unavailable` (`engine_failed`, `engine_not_installed`) |

## What is stored

- Evidence (`json`): selected, answering, failing (with messages) and skipped sources, limits
  reached, the number of discarded out-of-scope results and every in-scope name with the sources
  that reported it. Query strings and configured keys are removed from messages. Access category
  is `credentialed` when keys were used.
- Entities: the queried domain and one `domain` per subdomain, linked by derived `subdomain_of`
  relationships; observation `subdomain` per name with its sources.

## Scope

Only the domain itself and names ending in `.<domain>` are kept (wildcard prefixes removed);
anything else is discarded and counted. Absence from these datasets does not mean a subdomain does
not exist, and listed names may no longer resolve.

## Tests

`tests/test_connector_contracts.py` (stderr parsing against output recorded from the real binary
without network access, scope filtering, passive arguments, all sources failing, verified empty
result, missing key, rate limit, partial result with key redaction, result limit, fatal error,
missing engine, cancellation, input validation), `scripts/verify-phase2.sh` (real binary in the
collector: key-only source without a key is `authentication_required`, engine version recorded).
The real binary has not been run against live sources.

## Live verification log

None.
