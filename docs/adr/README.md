# Architecture decision records

Each record states the decision, the context that forced it, the alternatives considered and the
consequences accepted. They are written when a decision constrains later work, and they are not
rewritten afterwards: a decision that changes gets a new record that supersedes the old one.

| ADR | Decision | Mainly affects |
| --- | --- | --- |
| [0001](0001-phase-0-technology-baseline.md) | Phase 0 technology baseline and version selection | Whole stack |
| [0002](0002-local-authentication-and-sessions.md) | Local administrator bootstrap, sessions and request-forgery protection | `api`, `web` |
| [0003](0003-deployment-topology-and-secrets.md) | Compose topology, secrets delivery and least privilege | Compose, all services |
| [0004](0004-case-evidence-and-execution-lifecycle.md) | Case authorization, evidence storage and durable query execution | `api`, `worker`, `dispatcher` |
| [0005](0005-evidence-grounded-ai.md) | Evidence-grounded AI with an explicit pipeline, local models and server-side validation | `ai-worker`, `api` |
| [0006](0006-public-source-collection.md) | Public-source collection with a guarded collector, truthful outcomes and pinned engines | `collector` |
| [0007](0007-subfinder-network-sandbox.md) | Network sandbox and egress gateway for Subfinder | `discovery-runner`, `discovery-gateway` |
| [0008](0008-authorized-imports-and-document-processing.md) | Authorized imports with durable processing (WhatsApp exports, PDFs) | `worker` |
| [0009](0009-social-connector-capabilities.md) | Capability-based social platform connectors | `collector`, Sources screen |
| [0010](0010-team-roles-and-case-membership.md) | Team roles, case membership and administration without case access | `api`, `web` |
| [0011](0011-durable-monitoring-budgets-and-change-detection.md) | Durable monitoring, shared budgets and evidence-based change detection | `dispatcher`, `collector`, `api` |
| [0012](0012-notifications-and-webhook-adapter.md) | In-app notifications and an optional, redacted webhook adapter | `api`, `worker` |
| [0013](0013-stix-exchange-subset.md) | A documented STIX 2.1 subset without attribution or identity merging | `api` |
| [0014](0014-audit-trail-and-retention.md) | Transactional audit trail and retention through the deletion lifecycle | `api`, `worker` |

## Writing a new record

Copy the structure of an existing record: title, status, context, decision, alternatives and
consequences. Number it after the highest existing record. Record a decision when it is hard to
reverse, when it constrains how later features must behave, or when a reviewer would otherwise ask
"why was it not done the obvious way?".
