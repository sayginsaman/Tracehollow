# Roles and permissions

Tracehollow has three account roles (**administrator**, **analyst**, **viewer**) and two case
membership roles (**analyst**, **viewer**). They answer two separate questions:

- **What may this account do to the installation?** Decided by the account role alone
  (system permissions).
- **What may this account do in this case?** Decided by the account's *effective case role*: its
  membership role, capped by its account role. A viewer account is always a viewer member. An
  administrator is not a member of any case unless someone adds it.

The rules live in one module, `services/api/app/auth/permissions.py`, and are enforced by the API on
every route and again by background work. The web interface hides actions a role cannot use, but
hiding is a convenience; the API decision is the control. Verified by
`services/api/tests/test_team_access.py` and, against the running stack, by the `roles` stage of
`scripts/verify-phase5.sh`.

## Administration is not case access

Administrators manage accounts, case access, connector credentials and notification destinations,
and read the system audit log. **They do not see investigation content.** The case directory at
**Administration → Case access** lists titles, status, member counts and whether a case still has
an active analyst; nothing else. To open a case, an administrator adds itself as a member there,
which is an explicit, audited act (`membership.added`). Without membership, case routes answer 404,
exactly as for a case that does not exist.

## System permissions (account role)

| Permission | Administrator | Analyst | Viewer |
| --- | :---: | :---: | :---: |
| Create cases (`cases.create`) | yes | yes | no |
| Search accounts by username to add members (`accounts.search`) | yes | yes | no |
| Create accounts, change account roles, deactivate, reset passwords (`accounts.manage`) | yes | no | no |
| Manage members of any case without seeing its content (`case_access.manage`) | yes | no | no |
| Set or delete connector credentials (`credentials.manage`) | yes | no | no |
| Manage webhook destinations (`notification_destinations.manage`) | yes | no | no |
| Read the system audit log (`audit.system.read`) | yes | no | no |

Every signed-in account can change its own password, read its own notifications and read the
Sources page (capabilities, not credentials).

## Case permissions (effective case role)

"Analyst" and "Viewer" below mean the effective role in that case. A dash means the API refuses
with 403 `insufficient_case_role`, records a denied audit event and returns no case content.

| Area | Analyst | Viewer |
| --- | --- | --- |
| Case record, entities, relationships, observations, graph, timeline, notes, comparison | read and change | read |
| Evidence list, detail, text preview | read | read |
| Download a single evidence file | yes | yes |
| Evidence import (text, JSON, WhatsApp, PDF, STIX bundle), evidence deletion | yes | — |
| Saved queries and executions | create, run, cancel | read |
| Monitors, occurrences, change sets | create, edit, run, pause, resume, disable, delete | read |
| Webhook subscriptions of a monitor | add, remove, preview | — |
| Collection budgets | read and set | read |
| AI: conversations, answers, citations, summaries, suggestions, index status | read and request | read |
| AI keyword search of indexed text (no model is called) | yes | yes |
| AI questions, summaries, relationship suggestions, index rebuild or cancel, case AI setting | yes | — |
| Exports: JSON, CSV, STIX 2.1 bundle and export report | yes | — |
| HTML reports (preview and download) | yes | — |
| Members | add, change role, remove | read |
| Retention policy | preview, activate, deactivate, apply now | read policy and job history |
| Case audit log | read | — |
| Archive, restore, delete the case | yes | — |

### Viewer policy for AI and exports

Viewers may read everything in a case they belong to, including single original evidence files, so
they can check a finding against its source. They may not:

- **start model-backed AI requests.** Questions, summaries and suggestions consume local model time
  or, where allowed, send excerpts to a cloud provider; the analyst who owns the case decides that.
  Existing answers and their citations stay readable, and keyword search is allowed because it only
  queries the local index.
- **create bulk exports or reports.** A JSON, CSV or STIX export or an HTML report moves the whole
  case, or large parts of it, out of the application in one step. Reading record by record stays
  possible.

### Archived cases

Reads, exports, reports, member changes, retention changes, pausing or disabling monitors,
cancellation, restoring and deletion work on archived cases for analysts. Changes to content,
collection, imports, AI requests and enabling monitors need the case to be active. Archiving a case
pauses its enabled monitors.

## Background work re-checks authorization

A permission checked when work is queued is checked again when it runs:

| Work | Checked against | When access is gone |
| --- | --- | --- |
| Query execution | the requesting analyst | stops before the next page with `authorization_revoked`; collected pages are kept |
| Monitor occurrence | the analyst who last enabled it | the slot is skipped, the monitor pauses with `authorization_lost` and case analysts are notified |
| AI request | the requesting analyst | stops with `authorization_revoked` |
| Processing job (WhatsApp, PDF) | the importing analyst | stops with `authorization_revoked` |
| Webhook delivery | the analyst who created the subscription | the delivery is blocked with `authorization_lost` |
| In-app notification | the recipient's current membership | hidden from the list and refused when opened |

Access is gone when the member is removed, demoted to viewer, their account is demoted to viewer or
deactivated, or the case leaves the active state. Deactivating an account also revokes its sessions.

## Lockout protection

- The last active administrator cannot be demoted or deactivated (409 `last_administrator`).
  Concurrent changes by two administrators are serialized.
- A case's last active analyst cannot be removed or demoted by case members (409 `last_analyst`).
- Account changes that leave cases without an active analyst are allowed (an employee leaving must
  lose access at once) and reported: the response lists those cases, the Accounts page links to
  **Case access** filtered to cases without an analyst, and an administrator can add a new analyst
  there.
- An account forgotten by everyone can still be recovered on the host:
  `docker compose exec api python -m app.cli reset-password --username <name>`.

## Accounts

Accounts are local (username and password); there are no email invitations, single sign-on or
external identity providers. Administrators create accounts with an initial password and share it
privately; users change it under **Preferences** (other sessions of the account are signed out).
Migration `0006` converted the earlier `is_admin` flag: administrators became administrator accounts,
everyone else analyst accounts, and every existing case owner an analyst member.
