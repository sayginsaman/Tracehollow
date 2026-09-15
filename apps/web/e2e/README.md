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

Do not point the test at an installation that holds real investigation data: it signs in as the
given account and deletes the case it created, but it does create records and runs.
