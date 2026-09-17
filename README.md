# Tracehollow

Tracehollow is a self-hosted OSINT investigation workspace. It runs on your own machine with Docker
Compose: cases and evidence live in PostgreSQL, collection happens in isolated containers, and the
optional AI answers questions from a local model using only the evidence in the case, with citations
that open the exact passage in the original file.

It is built for work that has to hold up later. Every record keeps its provenance, every run keeps
its outcome, and the interface distinguishes what was collected, what an analyst asserted and what a
model produced.

![The Tracehollow workspace overview: recent cases, recent collection runs and imports, and the
state of required services](docs/screenshots/01-workspace-overview.jpg)

All screenshots in this repository show a synthetic demonstration case about a fictional company.
No real person, account or investigation appears in them.

## What you can do

- **Keep cases with evidence you can defend.** Imports and collected material are stored with an
  import origin, a SHA-256 hash, inert previews and hash-verified downloads.
- **Collect from public sources** through five connectors that run in their own container: public
  web pages, RSS/Atom feeds, GitHub accounts, username discovery across 58 platforms, and passive
  subdomain discovery in a network sandbox.
- **Import authorized material**: WhatsApp chat exports (Android and iOS layouts) and PDF
  documents, processed by a worker with no route to the internet.
- **Record entities and relationships** with identifiers, review status and supporting evidence.
  Matching identifiers are shown as leads; nothing is ever merged automatically.
- **Ask questions in the case** and get answers split into labelled claims, each citing a passage
  in a hash-verified record. Claims whose citations do not support them are removed before you see
  them.
- **Watch a source over time** with scheduled monitors, per-case and per-monitor budgets, and change
  reports that compare a run with the last complete comparable collection.
- **Work as a team**: administrator, analyst and viewer roles with per-case membership, an audit
  trail, and retention that needs a preview and a typed confirmation.
- **Hand results over**: JSON and CSV exports with a manifest, a STIX 2.1 subset, and self-contained
  HTML reports whose citations resolve offline.

## Quick start

Requirements: Docker Engine or Docker Desktop with Compose v2, `bash`, and `openssl` or
`/dev/urandom`. Nothing else is needed to run the stack; no paid API, cloud account or language
model is required.

```bash
git clone https://github.com/sayginsaman/Tracehollow.git
cd Tracehollow

# Create .env and generate local secrets under secrets/. Safe to re-run; it never overwrites.
scripts/setup.sh

# Build the images and start the core services, waiting until they report healthy.
docker compose up --build --detach --wait

# Print the one-time setup token.
cat secrets/bootstrap_token
```

Open <http://localhost:3000>. You are redirected to **Create the administrator**: paste the setup
token, choose a username and a password of at least 12 characters, then sign in. Choose your own
password; this repository contains no account and no example credential you could reuse.

The services bind to `127.0.0.1` by default. `docker compose down` and `docker compose up --detach`
keep your data; the named volumes hold the database, the broker and the evidence files.

Optional extras, each documented separately:

| Extra | What it adds | Guide |
| --- | --- | --- |
| Local AI | Evidence-grounded answers, summaries and relationship suggestions from a local Ollama model | [docs/operations/ai-models.md](docs/operations/ai-models.md) |
| OCR | Tesseract for scanned PDFs, built only with `TRACEHOLLOW_INSTALL_OCR=true` | [docs/operations/document-processing.md](docs/operations/document-processing.md) |
| Connector credentials | GitHub, Instagram Graph API, Telegram Bot API and YouTube Data API access | [docs/connectors/README.md](docs/connectors/README.md) |

## An investigation, end to end

The walkthrough below follows the synthetic demo case that ships with the documentation. You can
recreate it with [`scripts/seed_demo.py`](scripts/seed_demo.py); see
[docs/guides/demo-dataset.md](docs/guides/demo-dataset.md).

**1. Open a case.** A case records its purpose and scope, what has been collected and what still
needs a decision.

![A case overview showing purpose and scope, recent runs and imports, an analyst note, and a record
count of evidence, entities, relationships, saved queries, runs and notes](docs/screenshots/02-case-overview.jpg)

**2. Inspect the evidence.** Each record shows how it was acquired, when it was collected and
published, its SHA-256 and its content as inert text with line numbers.

![An evidence record with an authorized-import label, a hash-verified badge, line-numbered content
and a provenance panel showing acquisition, import origin, source dates and SHA-256](docs/screenshots/03-evidence-provenance.jpg)

**3. Follow the relationships.** The graph is bounded and always explains where a link came from.

![The relationship graph with an entity inspector showing the entity type, an analyst-assertion
origin and the number of relationships in view](docs/screenshots/04-relationship-graph.jpg)

**4. Ask the case a question.** The answer is produced locally and every claim carries a citation
that opens the exact location in the original record.

![An AI answer whose claims are labelled sourced, with a citation panel showing the JSON location in
the original document, the quoted text and a SHA-256-verified evidence link](docs/screenshots/05-ai-answer-citation.jpg)

**5. Keep the times apart.** Publication, local and collection times are never mixed into one
"happened at".

![A timeline of observations grouped by UTC date, each item labelled with the time basis the source
reported](docs/screenshots/06-timeline.jpg)

**6. Watch for changes.** A monitor reruns a saved query on a schedule, inside a budget, and records
what each run found compared with the previous complete collection.

![A monitor detail page with its schedule, daylight-saving rules, budget use and a table of
occurrences, one of which reports one new and one changed item](docs/screenshots/07-monitor-detail.jpg)

![A change report listing one changed feed entry with its before and after values and one new entry,
each linking to the evidence on both sides](docs/screenshots/08-change-detail.jpg)

**7. Hand it over.** Reports are built from records you select, previewed exactly as they will be
downloaded, and contain no scripts or external requests.

![A report preview showing the label legend and an AI-generated answer whose claims cite bundled
evidence excerpts by JSON location and line](docs/screenshots/09-report-preview.jpg)

## Capabilities and how far each one is verified

Tracehollow distinguishes what is implemented from what has been checked against a real service.
"Fixture-tested" means verified against controlled fixtures and the running stack, not against the
live platform.

| Capability | State |
| --- | --- |
| Cases, evidence, entities, relationships, exports | Implemented, verified in the stack |
| Public web page, RSS/Atom feed, GitHub account | Live-verified once each on 2026-09-15, for one authorized target per connector |
| Username discovery (Sherlock, 58 platforms) | Live-verified on 3 of 58 platforms (GitHub, GitLab, Codeberg); the rest fixture-tested |
| Passive subdomain discovery (Subfinder) | Fixture-tested; the live check was partial (one provider answered, one returned 403) |
| WhatsApp imports | Implemented for authorized exports you supply; tested with synthetic exports only. Not a way to read private conversations |
| PDF text extraction, optional OCR | Implemented, fixture-tested; OCR is a separate labelled record |
| Instagram | Graph API Business Discovery for professional accounts, plus an off-by-default public profile lookup. Fixture-tested. No private-profile or unrestricted personal-account access |
| Telegram | Public channel previews and Bot API chat metadata. Fixture-tested. No user sessions, joining or messaging |
| YouTube | Channel uploads and video comments through the Data API. Fixture-tested. **Transcripts are not available** |
| Evidence-grounded AI with a local model | Implemented; evaluated on a versioned synthetic set of 41 questions with a frozen holdout |
| Cloud AI generation (Anthropic, opt-in per case) | Implemented against documentation and mocked responses; **never called live** |
| Monitors, budgets, change detection, notifications | Implemented; verified against a controlled fixture source. No real source was monitored and no real notification service was contacted |
| Roles, case membership, audit trail, retention | Implemented, verified in the stack. The audit trail is transactional but not tamper-evident |
| STIX 2.1 subset | Implemented for export and bounded, idempotent import |
| MISP and OpenCTI | **Deferred**, not implemented |
| Continuous integration on a hosted runner | **Not yet run.** The workflow exists; no run has happened |

[docs/STATUS.md](docs/STATUS.md) records the acceptance criteria, evidence and limitations per
phase.

## Architecture

```
                      browser (127.0.0.1)
                              |
                    edge network
                  /                 \
             web (Next.js)      api (FastAPI)
                                     |
                              data network (internal, no internet route)
              +--------------+-------+--------+---------------+
              |              |                |               |
        postgres         redis          worker          dispatcher
     (cases, evidence   (broker    (imports, documents,   (outbox relay,
      index, outbox,     only)      retention, exports)    monitor slots)
      pgvector)
              |                                |
        evidence volume                   collector -----> collect-egress ----> public sources
                                               |
                                          discovery ----> discovery-gateway --> passive datasets
                                               |               (allowlist, verified TLS)
                                       discovery-runner
                                        (Subfinder, no direct route out)

        ai-worker ----> ai-egress ----> local Ollama (optional; cloud only if a case allows it)
```

PostgreSQL is authoritative: cases, evidence metadata, queries, runs, the outbox, monitor
schedules, budgets and the vector index all live there. Redis is only a broker. Evidence files are
stored on a separate volume and are never served as active content. The `worker` and `dispatcher`
have no internet route at all; anything that reaches the network does so from `collector`,
`discovery-runner` (through the gateway) or `ai-worker`.

## Documentation

Start at the [documentation index](docs/README.md).

| If you want to | Read |
| --- | --- |
| Install and run your first investigation | [docs/guides/first-investigation.md](docs/guides/first-investigation.md) |
| Set up the local model and grounded answers | [docs/operations/ai-models.md](docs/operations/ai-models.md) |
| Understand each source and its credentials | [docs/connectors/README.md](docs/connectors/README.md) |
| Import WhatsApp exports and documents | [docs/imports/whatsapp.md](docs/imports/whatsapp.md) |
| Run monitors, budgets and notifications | [docs/monitoring/README.md](docs/monitoring/README.md) |
| Manage roles and case membership | [docs/security/permissions.md](docs/security/permissions.md) |
| Produce reports and STIX exchange | [docs/analysis/README.md](docs/analysis/README.md), [docs/interoperability/stix.md](docs/interoperability/stix.md) |
| Back up, restore, upgrade and retain | [docs/operations/backup-restore.md](docs/operations/backup-restore.md), [docs/operations/retention.md](docs/operations/retention.md) |
| Fix a stuck stack | [docs/operations/troubleshooting.md](docs/operations/troubleshooting.md) |
| Develop, test or write a connector | [CONTRIBUTING.md](CONTRIBUTING.md), [docs/development/connectors.md](docs/development/connectors.md) |

Architecture decisions are recorded in [docs/adr](docs/adr/README.md); the product specification is
[PRD.md](PRD.md).

## Development

```bash
cd services/api && uv run ruff check . && uv run ruff format --check . && uv run mypy
cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build
scripts/test-backend.sh          # ephemeral PostgreSQL and Redis containers
make check                       # everything above
```

Stack-level acceptance scripts (`scripts/verify-phase0.sh` … `verify-phase5.sh`,
`scripts/verify-release.sh`) start isolated Compose projects on their own ports and volumes.
[CONTRIBUTING.md](CONTRIBUTING.md) explains the workflow, testing expectations and code style.

## Security and limitations

Report a vulnerability privately as described in [SECURITY.md](SECURITY.md); please do not open a
public issue for one.

What Tracehollow does not do:

- It does not bypass authentication, rate limits, private-profile restrictions or a platform's
  terms. Capability-dependent access stays capability-dependent.
- It does not merge identities. The same username on two platforms is a lead, never a person.
- It does not treat an empty result as an absence: blocked, rate-limited and failed checks are
  reported as themselves.
- It has not been reviewed by an independent security assessor, and it has not been run in a
  multi-tenant or internet-exposed deployment. Localhost deployment still requires authentication.
- Turkish and English are the languages its text handling has been tested with.

You are responsible for the legality of what you collect and process. Collect only material you are
authorized to collect.

## License

Tracehollow is released under the [MIT License](LICENSE). Third-party components keep their own
licenses; the notices are in [NOTICE.md](NOTICE.md) and the full dependency inventory, including
the copyleft components, is in [docs/licensing/dependencies.md](docs/licensing/dependencies.md).
No language model ships with Tracehollow; any model you download carries its own license.
