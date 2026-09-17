# Browser end-to-end tests

`phase1-workflow.spec.ts` drives the Phase 1 workflow in Chromium against a **running** stack:
case creation, entities, hostile and JSON evidence imports, relationship review, a saved query
executed twice, cancellation, graph edge inspection, JSON export and case deletion. It uses only
synthetic data and creates (then deletes) its own case.

```bash
cd apps/web
pnpm exec playwright install chromium        # once; downloads the browser build for @playwright/test

TRACEHOLLOW_E2E_BASE_URL=http://localhost:3000 \
TRACEHOLLOW_E2E_USERNAME=<administrator> \
TRACEHOLLOW_E2E_PASSWORD=<password> \
pnpm e2e
```

- On a fresh installation also set `TRACEHOLLOW_E2E_SETUP_TOKEN="$(cat ../../secrets/bootstrap_token)"`;
  the test then creates the administrator first.
- To use an already installed Chromium instead of downloading one, set
  `TRACEHOLLOW_E2E_CHROMIUM_EXECUTABLE=/path/to/chrome`.
- Without credentials the test is reported as **skipped**, not passed; check the summary line.
- `scripts/verify-phase1.sh --e2e` runs this test against its isolated verification stack.

Other specs:

- `phase2-sources.spec.ts`: the Sources screen and a public web page collected from the controlled
  `fixture-site` container. It only works against `scripts/verify-phase2.sh --e2e`, which provides
  that container (override the page with `TRACEHOLLOW_E2E_FIXTURE_PAGE`). Never point it at a real
  site without authorization.
- `phase3-ai.spec.ts`: import, indexing, a cited AI answer and the exact passage; run by
  `scripts/verify-phase3.sh --e2e`.

Do not point the test at an installation that holds real investigation data: it signs in as the
given account and deletes the case it created, but it does create records and runs.
- `phase4-workspace.spec.ts`: a WhatsApp export imported through the form with the date-order
  question answered in the browser, the timeline sections, an entity comparison, a report preview
  in a sandboxed frame and the Sources capability matrix. It needs the worker of
  `scripts/verify-phase4.sh --e2e`; it creates its own case.
- `workspace-shell.spec.ts`: the redesigned shell. Sign-in lands on Overview; a case is created
  from there, a synthetic PDF is imported through **Imports** and its extracted text opened, the
  theme preference is changed and restored, configuration pages are visited and the case is
  reopened from Case settings through the breadcrumb, and the navigation drawer is used on a
  phone-sized viewport. It needs a worker for PDF processing and deletes the case it creates.

All specs use the navigation described in [docs/design/README.md](../../../docs/design/README.md):
sign-in lands on `/overview`, **New case** and **New query** open their forms when the list is not
empty, and imports live under **Imports**.
- `phase5-monitoring.spec.ts`: a paused monitor created for the controlled feed with the
  recurring-collection confirmation, a run and its baseline change set, a viewer added as member and
  the case seen read-only by that viewer, and the administrator pages without access to the case.
  It needs the fixture feed and the accounts of `scripts/verify-phase5.sh --e2e`
  (`TRACEHOLLOW_E2E_ADMIN_USERNAME`, `TRACEHOLLOW_E2E_ADMIN_PASSWORD`, `TRACEHOLLOW_E2E_VIEWER_USERNAME`).
