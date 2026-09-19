# Release checklist

Work through this before tagging anything. A box is ticked only when the evidence exists, not when
the step looks likely to pass. Where a check has already been run for the current candidate, the
result is named.

## Code and tests

- [x] Backend lint, format and types clean (`ruff check`, `ruff format --check`, `mypy`)
- [x] Backend tests pass (`scripts/test-backend.sh`: 586 passed, 3 skipped)
- [x] Frontend lint, types, tests and production build pass (104 tests)
- [x] Migrations apply to a fresh database and to an existing one (install and upgrade drills)
- [x] Stack acceptance scripts pass for the phases in scope
- [x] **Continuous integration passes on a hosted runner.** Green on 2026-09-19 (`f787f12`,
      run 35469135321): backend, frontend and the full stack verification, phases 0-5 with browser
      workflows, in 39 minutes. It took three failed runs to get there, and all three faults were in
      the checks rather than in the product.

## Drills

- [x] Clean install from a clean checkout, using only the documented quick start
- [x] Upgrade from the previous recorded state with synthetic data, verifying that cases,
      memberships, evidence, citations, saved queries, monitors and settings survive
- [x] Backup and restore into a separate installation: hashes match, previews and citations
      resolve, membership restrictions hold, monitors are paused, no queued collection resumes and
      no external notification is sent
- [ ] Restore rehearsed by someone other than the author, following only the written procedure

## Interface

- [x] axe-core (WCAG 2.0/2.1/2.2 A and AA) clean on the main pages in light and dark themes
- [x] Every visible keyboard stop has a focus indicator
- [x] No horizontal overflow at 390, 768 and 1280 px, or at 200% text zoom
- [x] A second engine (WebKit) renders the same pages without console or page errors
- [ ] Screen-reader pass with a real screen reader
- [ ] Contrast reviewed by a person, not only by automated rules

## Documentation

- [x] README describes what exists, with screenshots of the running application
- [x] Documentation index, tutorial, how-to guides, reference and explanation in place
- [x] Every command, environment variable and path in the documentation checked against the code
- [x] Relative links and image links resolve
- [x] Screenshots use synthetic demonstration data only, and say so
- [x] Performance and interface measurements published with their machine, dataset and method

## Licensing and provenance

- [x] Dependency and license inventory generated from the installed packages
- [x] `NOTICE.md` lists what ships inside the images, including copyleft components
- [x] `LICENSE` and `NOTICE.md` are copied into the images
- [x] The repository's license reconciled with the PRD's proposal and the change recorded, not
      silently made
- [x] **Owner decided Apache-2.0** on 2026-09-19, before the first tag. The owner is the sole
      copyright holder; commits published under MIT before that date remain available under MIT

## Security and privacy

- [x] Tracked files and Git history scanned for credentials, tokens, real investigation data and
      private exports; only synthetic placeholders found
- [x] `SECURITY.md` describes the current surfaces and the disclosure route
- [x] No real credential, password or personal data in the repository, the demo dataset or the
      screenshots
- [ ] Independent security review
- [ ] Dependency and container vulnerability scanning automated

## Publication

- [x] The public repository carries the code and the clone command in the README works
      (github.com/sayginsaman/Tracehollow, pushed 2026-09-17)
- [x] Version decided and tagged: `v0.1.0`
- [x] Release notes published from [v0.1.0.md](v0.1.0.md) with the limitations section intact
- [ ] Decide whether prebuilt images are published; if they are, the notices in the images and the
      base-image licenses (including Redis 8) apply to the distribution. Nothing is published for
      0.1.0: users build the images themselves from the quick start

## Do not tick unless true

The point of this file is to keep a release honest. 0.1.0 ships with four boxes still open: no
restore rehearsed by a second person, no screen-reader pass, no human contrast review, and no
independent security review. They are named in the release notes rather than quietly left out.
