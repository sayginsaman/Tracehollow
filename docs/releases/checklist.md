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
- [ ] **Continuous integration passes on a hosted runner.** Never run. A workflow file is not
      evidence; this stays open until an actual run is green.

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
- [x] The repository's actual license (MIT) reconciled with the PRD's proposal (Apache-2.0) and the
      difference recorded, not silently changed
- [ ] **Owner decides** whether the project ships under MIT or Apache-2.0 before the first tag.
      Changing it later is a relicensing question, not an edit

## Security and privacy

- [x] Tracked files and Git history scanned for credentials, tokens, real investigation data and
      private exports; only synthetic placeholders found
- [x] `SECURITY.md` describes the current surfaces and the disclosure route
- [x] No real credential, password or personal data in the repository, the demo dataset or the
      screenshots
- [ ] Independent security review
- [ ] Dependency and container vulnerability scanning automated

## Publication

- [ ] Confirm the public repository URL and that the clone command in the README works after the
      first push. The repository currently holds only an initial commit, so the command in the
      README will not produce a working checkout until then
- [ ] Decide the version number and tag (`0.1.0` proposed for the first release)
- [ ] Publish the release notes from [v0.1.0-rc.1.md](v0.1.0-rc.1.md), keeping the limitations
      section intact
- [ ] Decide whether prebuilt images are published; if they are, the notices in the images and the
      base-image licenses (including Redis 8) apply to the distribution

## Do not tick unless true

The point of this file is to keep a candidate honest. If CI has not run, licensing is undecided, or
the restore has only ever been performed by the person who wrote it, the candidate is not a release,
however complete the feature list looks.
