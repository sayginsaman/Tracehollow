# Interface design

The UI/UX redesign that sits between Phase 4 and Phase 5. It changes how the workspace looks and is
navigated; it does not change what the backend collects, stores, verifies or claims. Strategic
context is in [PRODUCT.md](../../PRODUCT.md), the design system in [DESIGN.md](../../DESIGN.md)
(machine-readable extensions in [DESIGN.json](../../DESIGN.json)).

## Baseline review (before)

Screens were walked through with synthetic demo cases and captured at 1440, 1280, 768 and 390px
(`screenshots/before/`). Concrete problems found:

- **Navigation:** a top bar with three links; case sections were eleven tabs that wrapped into five
  rows on a phone. There was no overview of work across cases, and the case context disappeared on
  global pages without a way back other than the browser.
- **Hierarchy and density:** every page opened with a create form above the list it belonged to
  (New case above the case list, three import forms above the evidence list), so the primary
  content started far below the fold. Record counts were shown as a grid of metric boxes.
- **Sources:** an 11,350px page (19,700px on a phone) listing every field of every connector and
  capability with equal weight.
- **Typography:** monospace for every timestamp and table date; evidence previews in monospace
  regardless of content; uppercase table headers.
- **Evidence and citations:** imports, processing jobs and the evidence list were one page;
  provenance was a single "Acquisition" column that did not distinguish extracted or OCR text from
  the imported original; raw JSON payloads were shown by default on entity and evidence pages.
- **Runs:** lists showed only `Completed`/`Failed`, not whether a run found nothing, needed
  credentials or was rate-limited; quota details were raw JSON.
- **Responsive:** page-level horizontal overflow on run detail (390 and 768px) and Sources (390 and
  768px); the graph labels overlapped.
- **States:** loading states were a line of text; empty states said "No … yet" without saying what
  to do; destructive actions such as deleting an entity or a note had no confirmation step.
- **Honesty gaps in copy:** Environment status still said the only connector was a synthetic
  fixture and that AI was unavailable.

## What changed

### Shell and navigation

| Area | Routes | Notes |
| --- | --- | --- |
| Workspace | `/overview`, `/cases` | `/` and sign-in now land on Overview; `/cases?new=1` opens the create form |
| Current case (grouped by task) | `/cases/{id}` and `…/queries`, `…/runs/{run}`, `…/imports` (new), `…/evidence`, `…/entities`, `…/relationships`, `…/graph`, `…/timeline`, `…/compare`, `…/ai`, `…/reports`, `…/settings` | All existing case routes are kept; `…/compare?entity_id=…` preselects entities |
| Configuration | `/sources`, `/status`, `/preferences` (new) | Credentials stay on Sources and remain admin-only and write-only |
| Authentication and errors | `/login`, `/setup`, root not-found page, workspace error boundary | Dependency errors keep their wording |

- A 248px sidebar (drawer below 1024px, focus moved in and back, Escape closes) and a sticky header
  with breadcrumbs that always name the current case and, on detail pages, the record.
- Global pages moved into the `(workspace)/(global)` route group so the sidebar can show case
  sections inside a case. URLs are unchanged.

### Design system

- OKLCH tokens in `apps/web/src/app/globals.css`; the default Tailwind palette is removed. Light
  theme primary, dark theme maintained with the same roles, stored preference on `/preferences`.
- IBM Plex Sans (variable) and IBM Plex Mono bundled from `@fontsource` packages (OFL-1.1) and
  lucide icons (ISC). No font server or third-party request.
- Shared primitives in `apps/web/src/components/ui/`: buttons, fields and choice fields, panels and
  page headers, data tables with contained scrolling, tabs and segmented filters, status labels,
  provenance labels, notices, skeleton loading, empty and error states, confirmation steps, copyable
  long values and timestamps. The old `ui.tsx`, `StatusBadge.tsx`, `FormField.tsx`,
  `WorkspaceShell.tsx` and `CaseHeader.tsx` were replaced, not layered over.

### Screens

- **Overview:** items that need a decision or explain a failure (chat exports waiting for a date
  order, failed imports, failed or partial runs, failed deletions, unavailable services), recent
  cases, recent runs and imports, environment readiness. Every number comes from the API.
- **Cases:** search, status and tag filters, sorting, pagination; synthetic demo cases labelled.
- **Case overview:** purpose and scope as reading text, recent activity with plain-language
  outcomes, needs attention, records and case record; empty cases explain the three ways to start.
- **Imports (new page):** Text or JSON, WhatsApp export and PDF document as tabs, each with accepted
  formats, default limits and what happens shown before upload; processing jobs follow running work
  automatically and explain unknown timezones, date order, missing attachments and OCR state;
  cancel and process again; recent imports with provenance.
- **Evidence:** provenance labels, search and filters, a quick-look inspector, and a detail page
  with line-numbered content (sans for text, mono for JSON and HTML, page dividers for extracted
  PDF text), provenance and integrity in a side column, payloads behind disclosures.
- **Entities, relationships, graph:** create forms open on demand (and by default in an empty case),
  shared identifiers shown as leads with a compare link, supporting evidence chosen with checkboxes,
  relationship details beside the table, graph node and edge inspection with a visual legend.
- **Queries and runs:** saved queries and run history separated; results in plain language
  (completed with or without findings, partial, access or setup required, rate limited, failed,
  canceled); raw parameter snapshot behind a disclosure.
- **Timeline:** sections as tabs with counts, items grouped by day in their own basis (UTC, local
  date, collection date).
- **Compare:** shared identifiers, differences, conflicts, relationships, changes over time and
  unknowns as separate sections.
- **AI:** starter questions, local-only indicator, AI-generated label on every answer, citation
  panel beside the conversation, honest progress and failure states with Ask again. No confidence
  percentages.
- **Reports:** filterable pickers, redaction, a preview that must precede download; the file stays
  inert with offline citations.
- **Sources, Environment status, Preferences:** Sources is about half as long, with capability
  availability visible and field lists behind disclosures; Environment status copy corrected.

### Backend additions (read-only, additive)

The interface needed three things existing endpoints could not provide without one request per case
or per run. All are covered by tests in `services/api/tests/test_activity.py` and
`tests/test_cases.py`:

- `GET /api/v1/activity`: recent runs and processing jobs, active counts and jobs waiting for input
  across the cases the user is a member of (active or archived only).
- `connector_outcomes` on run lists, for plain-language outcomes.
- `sort` on `GET /api/v1/cases` (updated, created or title).

## Verification

Environment: an isolated Compose project (`tracehollow-design`, web on 127.0.0.1:3150) with the
controlled fixture site, the synthetic AI provider and OCR, filled by
`scripts/seed_demo_workspace.py`. The default project's volumes were not used.

| Check | Result |
| --- | --- |
| `pnpm lint`, `pnpm typecheck` | pass |
| `pnpm test` (Vitest) | 15 files, 83 tests pass (the 66 existing tests unchanged, 17 new) |
| Production build (`docker compose build web`) | pass |
| Browser workflows (Playwright, production build) | 5 specs pass: `phase1-workflow`, `phase2-sources`, `phase3-ai`, `phase4-workspace` (steps updated for the new navigation, assertions unchanged) and the new `workspace-shell` (PDF import, settings and back, theme, phone drawer) |
| axe-core 4.10 (WCAG 2.0/2.1/2.2 A and AA, best practices) on 22 routes | 0 violations in the light and the dark theme |
| Page-level horizontal overflow on every route | none at 1440, 1280, 768 and 390px, in the dark theme, and at 200% zoom (720 CSS px at 2x) |
| Page errors during capture | none |
| Keyboard | skip link is the first stop and moves focus to the content; the drawer takes focus and returns it, Escape closes it (browser); tabs move with arrow keys, Home and End (unit test) |
| Impeccable anti-pattern detector (`impeccable --json apps/web/src`) | 0 findings |
| `scripts/verify-phase4.sh --ocr --e2e` (now also runs `workspace-shell.spec.ts`) | 146 checks pass, including both browser workflows |

## Screenshots

Before and after pairs are in [`screenshots/`](screenshots/) as WebP (1440px captures scaled to
1000px wide; phone captures at 390px). Before, imports lived on the Evidence page and there was no
Overview or Preferences page.

| Screen | Before | After |
| --- | --- | --- |
| Sign in | [1440](screenshots/before/login-1440.webp) | [1440](screenshots/after/login-1440.webp) |
| Landing after sign-in | [Cases 1440](screenshots/before/cases-1440.webp) | [Overview 1440](screenshots/after/overview-1440.webp), [390](screenshots/after/overview-390.webp) |
| Cases | [1440](screenshots/before/cases-1440.webp), [390](screenshots/before/cases-390.webp) | [1440](screenshots/after/cases-1440.webp), [390](screenshots/after/cases-390.webp) |
| Case overview | [1440](screenshots/before/case-overview-1440.webp), [390](screenshots/before/case-overview-390.webp) | [1440](screenshots/after/case-overview-1440.webp), [1280](screenshots/after/case-overview-1280.webp), [390](screenshots/after/case-overview-390.webp) |
| Evidence and imports | [1440](screenshots/before/evidence-1440.webp), [390](screenshots/before/evidence-390.webp) | [Evidence 1440](screenshots/after/evidence-1440.webp), [Imports 1440](screenshots/after/imports-1440.webp), [Imports 390](screenshots/after/imports-390.webp) |
| Evidence record (chat text) | [1440](screenshots/before/evidence-chat-1440.webp), [390](screenshots/before/evidence-chat-390.webp) | [1440](screenshots/after/evidence-chat-1440.webp), [390](screenshots/after/evidence-chat-390.webp) |
| Entity | [1440](screenshots/before/entity-detail-1440.webp) | [1440](screenshots/after/entity-detail-1440.webp) |
| Queries & runs | [1440](screenshots/before/queries-1440.webp), [390](screenshots/before/queries-390.webp) | [1440](screenshots/after/queries-1440.webp), [768](screenshots/after/queries-768.webp), [390](screenshots/after/queries-390.webp) |
| Failed run | [1440](screenshots/before/run-failed-1440.webp), [390](screenshots/before/run-failed-390.webp) | [1440](screenshots/after/run-failed-1440.webp), [390](screenshots/after/run-failed-390.webp) |
| Graph | [1440](screenshots/before/graph-1440.webp) | [1440](screenshots/after/graph-1440.webp) |
| Timeline | [1440](screenshots/before/timeline-1440.webp), [390](screenshots/before/timeline-390.webp) | [1440](screenshots/after/timeline-1440.webp), [390](screenshots/after/timeline-390.webp) |
| Compare | [1440](screenshots/before/compare-1440.webp) | [1440](screenshots/after/compare-1440.webp) |
| AI conversation | [1440](screenshots/before/ai-conversation-1440.webp), [390](screenshots/before/ai-conversation-390.webp) | [1440](screenshots/after/ai-conversation-1440.webp), [390](screenshots/after/ai-conversation-390.webp) |
| Reports | [1440](screenshots/before/reports-1440.webp) | [1440](screenshots/after/reports-1440.webp) |
| Sources | [1440](screenshots/before/sources-1440.webp), [390, first 16,000px](screenshots/before/sources-390.webp) | [1440](screenshots/after/sources-1440.webp), [390](screenshots/after/sources-390.webp) |
| Environment status | [1440](screenshots/before/status-1440.webp) | [1440](screenshots/after/status-1440.webp) |
| Case settings | [1440](screenshots/before/case-settings-1440.webp) | [1440](screenshots/after/case-settings-1440.webp) |
| Preferences, not found, API unavailable | none | [Preferences](screenshots/after/preferences-1440.webp), [Not found](screenshots/after/not-found-1440.webp), [Sign in without API](screenshots/after/unavailable-login-1440.webp) |
| Dark theme | none | [Case overview](screenshots/after/dark-case-overview-1440.webp), [390](screenshots/after/dark-case-overview-390.webp), [Evidence](screenshots/after/dark-evidence-chat-1440.webp), [AI](screenshots/after/dark-ai-conversation-1440.webp), [Timeline](screenshots/after/dark-timeline-1440.webp) |
| 200% zoom, phone drawer | none | [Evidence at 200%](screenshots/after/zoom200-evidence-text-720.webp), [Case overview at 200%](screenshots/after/zoom200-case-overview-720.webp), [Drawer](screenshots/after/drawer-390.webp) |

## Reproduce

```bash
# A disposable stack with the fixture site, synthetic AI and OCR (never the default project)
COMPOSE_PROJECT_NAME=tracehollow-design COMPOSE_FILE=compose.yaml:compose.verify-phase4.yaml \
TRACEHOLLOW_WEB_PORT=3150 TRACEHOLLOW_API_PORT=8150 TRACEHOLLOW_AI_LOCAL_PROVIDER=synthetic_fixture \
TRACEHOLLOW_INSTALL_OCR=true docker compose up --detach --wait

# Synthetic demo cases ("[Synthetic demo] …", tag synthetic-demo). PDFs: the JSON that
# scripts/verify-phase4.sh generates in its work directory (text, scanned, encrypted).
TRACEHOLLOW_DEMO_PASSWORD='<at least 12 characters>' python3 scripts/seed_demo_workspace.py \
  --web-url http://localhost:3150 --pdfs pdfs.json

# Every route at four widths plus an overflow report; set TRACEHOLLOW_SCREENS_COLOR_SCHEME=dark or
# TRACEHOLLOW_SCREENS_SCALE=2 with width 720 for 200% zoom
cd apps/web
TRACEHOLLOW_SCREENS_USERNAME=demo-analyst TRACEHOLLOW_SCREENS_PASSWORD='…' \
  node scripts/capture-screens.mjs /tmp/screens 1440,1280,768,390
```

## Known limitations

- Verified in Chromium only (Playwright build 1243) on macOS; Firefox, Safari, Windows and real
  mobile devices were not tested. Screen readers were not tested beyond the accessibility tree and
  axe; VoiceOver and NVDA walkthroughs remain open.
- 200% zoom was emulated with a 720 CSS px viewport at 2x device scale, not with browser zoom.
- Touch targets meet WCAG 2.2 AA (24px) but compact table actions are 32px, below the 44px
  recommended for touch-first use.
- The graph is still a canvas; its keyboard and screen-reader equivalent is the edge table.
- Very long single-page tables (for example 100 evidence records) are paginated, not virtualised.
- No keyboard shortcuts or command palette.
- The dark theme was checked by axe and screenshots on key screens, not by a separate visual review
  of every state.
