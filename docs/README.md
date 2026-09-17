# Tracehollow documentation

Everything here describes behaviour that exists in this repository. Where something is implemented
but not verified against a live service, the page says so.

The documentation is organised by what you are trying to do. If you are new, start with the
tutorial; if you have a specific problem, use a how-to guide; if you need exact values, use the
reference; if you want to know why something works the way it does, read an explanation.

## Start here (tutorial)

| Guide | What you get |
| --- | --- |
| [Install and run your first investigation](guides/first-investigation.md) | A running stack, an administrator account, a case with imported evidence, an entity, a relationship and a report |

## Solve a problem (how-to guides)

| Task | Guide |
| --- | --- |
| Recreate the English demonstration dataset used in the screenshots | [guides/demo-dataset.md](guides/demo-dataset.md) |
| Set up the local model and ask evidence-grounded questions | [operations/ai-models.md](operations/ai-models.md) |
| Collect from public sources and store connector credentials | [connectors/README.md](connectors/README.md) |
| Import an authorized WhatsApp export | [imports/whatsapp.md](imports/whatsapp.md) |
| Process PDF documents, with or without OCR | [operations/document-processing.md](operations/document-processing.md) |
| Schedule monitors, set budgets and route notifications | [monitoring/README.md](monitoring/README.md) |
| Give people roles and case access | [security/permissions.md](security/permissions.md) |
| Build a timeline, compare entities and produce a report | [analysis/README.md](analysis/README.md) |
| Exchange STIX 2.1 objects | [interoperability/stix.md](interoperability/stix.md) |
| Back up, restore and upgrade | [operations/backup-restore.md](operations/backup-restore.md) |
| Delete data on a schedule | [operations/retention.md](operations/retention.md) |
| Fix a stack that will not start or a run that will not finish | [operations/troubleshooting.md](operations/troubleshooting.md) |
| Write a new connector | [development/connectors.md](development/connectors.md) |
| Run an authorized live check against a real source | [connectors/live-smoke.md](connectors/live-smoke.md) |

## Look something up (reference)

| Reference | Contents |
| --- | --- |
| [connectors/README.md](connectors/README.md) | Every connector: mode, coverage, limits, credentials, outcomes, capability matrix |
| [monitoring/budgets.md](monitoring/budgets.md) | What counts as a request, how reservations and reconciliation work |
| [security/permissions.md](security/permissions.md) | The full permission matrix per role and case membership |
| [interoperability/stix.md](interoperability/stix.md) | The supported STIX 2.1 subset, field by field |
| [testing/ai-evaluation](testing/ai-evaluation/README.md) | The AI evaluation set, metrics and recorded runs |
| [testing/performance.md](testing/performance.md) | Measured latency, accessibility and cross-engine results, with the machine and dataset they came from |
| [licensing/dependencies.md](licensing/dependencies.md) | Dependency and license inventory |
| [STATUS.md](STATUS.md) | Acceptance criteria, evidence and limitations per phase |
| [../PRD.md](../PRD.md) | The product specification the phases implement |

## Understand the design (explanation)

| Topic | Where |
| --- | --- |
| Why the interface looks and behaves as it does | [design/README.md](design/README.md) |
| Why each significant technical decision was taken | [adr/README.md](adr/README.md) |
| How collection stays truthful about absence and failure | [ADR 0006](adr/0006-public-source-collection.md) |
| Why AI answers are validated on the server | [ADR 0005](adr/0005-evidence-grounded-ai.md) |
| Why monitors never catch up after downtime | [ADR 0011](adr/0011-durable-monitoring-budgets-and-change-detection.md) |

## Contributing and security

- [CONTRIBUTING.md](../CONTRIBUTING.md): development setup, the checks to run, testing expectations
  and code style.
- [SECURITY.md](../SECURITY.md): the security model, the trust boundaries and how to report a
  vulnerability privately.
- [development/connectors.md](development/connectors.md): the connector contract a new source must
  satisfy.

## Screenshots

[screenshots/](screenshots) holds the images used in the README and the guides. They all show the
synthetic demonstration case from [guides/demo-dataset.md](guides/demo-dataset.md); no real person,
account or investigation appears in them.
