# Dependency and license inventory

Generated from the actual lockfiles and installed packages of the release candidate
(`services/api/uv.lock`, `apps/web/pnpm-lock.yaml`) and from the Dockerfiles. Regenerate it with the
commands at the end after changing dependencies. Code licenses, data licenses, provider terms and
model licenses are separate questions; this file covers code and bundled assets.

## Project license

Tracehollow's own code is published under the **MIT License** ([LICENSE](../../LICENSE),
`Copyright (c) 2026 Saygin D. Saman`). [PRD.md](../../PRD.md) §13 records Apache-2.0 as an earlier
*proposal*; the repository ships MIT and nothing here relicenses it. If Apache-2.0 is still wanted,
that is an owner decision to make before publication, not a documentation change.

Third-party code keeps its own license. Nothing in this repository is copied from another project's
source; dependencies are consumed as published packages, images or release binaries.

## What ships in the images

| Layer | Contents | Notes |
| --- | --- | --- |
| `tracehollow-api`, `tracehollow-collector` | Python 3.13 runtime dependencies (below) and the application | `python:3.13.15-slim-trixie` base, pinned by digest |
| `tracehollow-discovery-runner` | Subfinder 2.16.0 release binary, verified against a pinned SHA-256, with its `LICENSE.md` copied to `/usr/local/share/licenses/subfinder/` | MIT |
| `tracehollow-web` | Next.js production build, IBM Plex fonts and lucide icons | `node:24.21.0-trixie-slim` base, pinned by digest |
| `postgres` | `pgvector/pgvector:0.8.6-pg18-trixie`, pinned by digest | PostgreSQL License (PostgreSQL, pgvector); pulled, not redistributed by this project |
| `redis` | `redis:8.10.1-trixie`, pinned by digest | Redis 8 is licensed under AGPLv3/RSALv2/SSPLv1 by its vendor; used as an unmodified upstream image for the local broker, not redistributed or offered as a service |

Optional, built only with `TRACEHOLLOW_INSTALL_OCR=true`: Tesseract OCR with English and Turkish
data from Debian packages (Apache-2.0).

## Backend (Python)

Direct dependencies of `services/api` (runtime, collector engines and development groups):

| Package | Version | License |
| --- | --- | --- |
| `alembic` | 1.20.0 | MIT |
| `argon2-cffi` | 25.1.0 | MIT |
| `celery` | 5.6.3 | BSD-3-Clause |
| `celery-types` | 0.26.0 | Apache-2.0 |
| `cryptography` | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| `defusedxml` | 0.7.1 | PSFL |
| `fastapi` | 0.141.1 | MIT |
| `httpx2` | 2.13.0 | BSD-3-Clause |
| `mypy` | 2.3.1 | MIT |
| `psycopg` | 3.3.5 | LGPL-3.0-only |
| `pydantic` | 2.13.5 | MIT |
| `pydantic-settings` | 2.15.0 | MIT |
| `pypdf` | 6.19.0 | BSD-3-Clause |
| `pypdfium2` | 5.13.0 | BSD-3-Clause, Apache-2.0, dependency licenses |
| `pytest` | 9.1.1 | MIT |
| `python-multipart` | 0.0.32 | Apache-2.0 |
| `redis` | 6.4.0 | MIT |
| `ruff` | 0.16.7 | MIT |
| `sherlock-project` | 0.16.2 | MIT |
| `SQLAlchemy` | 2.0.53 | MIT |
| `stix2` | 3.0.2 | BSD |
| `types-defusedxml` | 0.7.0.20260504 | Apache-2.0 |
| `types-requests` | 2.33.0.20260906 | Apache-2.0 |
| `uvicorn` | 0.53.0 | BSD-3-Clause |

Including transitive dependencies: MIT (36), BSD-3-Clause (12), Apache-2.0 (7), BSD (6), BSD License (3), LGPL-3.0-only (2), Apache-2.0 OR BSD-3-Clause (1), PSFL (1) — 81 packages in total.

Licenses that need attention:

- **`psycopg` and `psycopg-binary` (LGPL-3.0-only)** — the PostgreSQL driver. Used unmodified as an
  installed library; anyone redistributing the images must keep the LGPL notice and the ability to
  replace the library. Upstream: <https://psycopg.org/>.
- **`stem` (LGPLv3)** — a transitive dependency of `sherlock-project`, present only in the
  collector image. Unmodified. Upstream: <https://stem.torproject.org/>.
- **`pypdfium2` (BSD-3-Clause and Apache-2.0, bundling PDFium)** — bundles the PDFium binary
  (BSD-3-Clause, with third-party components listed upstream).
- **`certifi` (MPL-2.0)** and **`pathspec` (MPL-2.0)** — unmodified libraries; MPL obligations
  apply per file and are satisfied by using the published packages.
- **`defusedxml` (PSFL)** — Python Software Foundation License.
- **`sherlock-project` (MIT)** — the username discovery engine, executed as a separate process.

## Frontend (JavaScript)

Direct dependencies of `apps/web`:

| Package | Version | License |
| --- | --- | --- |
| `@fontsource-variable/ibm-plex-sans` | 5.3.0 | OFL-1.1 |
| `@fontsource/ibm-plex-mono` | 5.3.0 | OFL-1.1 |
| `@playwright/test` | 1.63.0 | Apache-2.0 |
| `@tailwindcss/postcss` | 4.3.3 | MIT |
| `@testing-library/dom` | 10.4.2 | MIT |
| `@testing-library/jest-dom` | 7.0.1 | MIT |
| `@testing-library/react` | 16.3.3 | MIT |
| `@testing-library/user-event` | 14.6.7 | MIT |
| `@types/node` | 24.13.4 | MIT |
| `@types/react` | 19.3.0 | MIT |
| `@types/react-dom` | 19.3.0 | MIT |
| `@vitejs/plugin-react` | 6.1.1 | MIT |
| `cytoscape` | 3.34.3 | MIT |
| `eslint` | 9.39.5 | MIT |
| `eslint-config-next` | 16.3.5 | MIT |
| `jsdom` | 30.0.1 | MIT |
| `lucide-react` | 1.46.0 | ISC |
| `next` | 16.3.5 | MIT |
| `react` | 19.3.0 | MIT |
| `react-dom` | 19.3.0 | MIT |
| `server-only` | 0.0.1 | MIT |
| `tailwindcss` | 4.3.3 | MIT |
| `typescript` | 6.0.3 | Apache-2.0 |
| `vite` | 8.3.0 | MIT |
| `vitest` | 5.0.0 | MIT |

Including transitive dependencies: MIT (371), Apache-2.0 (28), ISC (18), BSD-2-Clause (9), MPL-2.0 (5), BSD-3-Clause (3), OFL-1.1 (2), BlueOak-1.0.0 (2) — 446 packages in total. Most are build-time only; the
production image contains the compiled application, the bundled fonts and the icon set.

Shipped assets:

- **IBM Plex Sans and IBM Plex Mono** (`@fontsource*`, OFL-1.1) — bundled so no font server is
  contacted. The OFL notice is reproduced in [NOTICE.md](../../NOTICE.md).
- **lucide-react** (ISC) — icon set.
- **cytoscape** (MIT) — the relationship graph canvas.

Licenses that need attention: `lightningcss` and `axe-core` (MPL-2.0) are build- and test-time
tools; `@img/sharp-libvips-*` (LGPL-3.0-or-later) is an optional image-processing binary pulled by
Next.js tooling on some platforms and is not required by the application at runtime.

## Models, data and providers

- **No model is bundled.** Optional local answering uses [Ollama](https://ollama.com) (MIT) with
  models the operator downloads themselves: `qwen3:8b` and `qwen3-embedding:0.6b` are Apache-2.0
  from their publisher, but the operator accepts whatever licence applies to the model they choose.
- **Optional cloud provider** (off by default): the provider's own terms apply to anything sent.
  Tracehollow sends nothing to a cloud provider unless a case is explicitly set to allow it.
- **Demo and test data** in this repository is synthetic, written for the project, and uses
  reserved example domains and documentation IP ranges.
- **Source access terms are separate from code licenses.** A connector's ability to read a platform
  does not grant rights over the collected material; see
  [docs/connectors/README.md](../connectors/README.md).

## Regenerating this inventory

```bash
# Python distributions actually installed for the API image
cd services/api && uv run --no-sync python -c "from importlib import metadata; [print(d.metadata['Name'], d.version, d.metadata.get('License-Expression') or d.metadata.get('License')) for d in metadata.distributions()]"

# JavaScript packages from the pnpm store
cd apps/web && node -e "…"   # see scripts/ for the snippet used to build this table
```

Check the Dockerfiles for bundled binaries and the base image digests; they are pinned there.
