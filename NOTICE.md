# Notices

Tracehollow
Copyright 2026 Saygin D. Saman

Licensed under the Apache License, Version 2.0 (see [LICENSE](LICENSE)). This file is the NOTICE
file that Section 4(d) of that license asks redistributors to carry, and it also lists the
third-party software below, which keeps its own license and copyright. A full inventory,
including transitive dependencies and the reasoning for the copyleft components, is in
[docs/licensing/dependencies.md](docs/licensing/dependencies.md).

This file is copied into the container images so that a redistributed image carries the notices of
the material bundled in it.

## Bundled in the web image

**IBM Plex Sans and IBM Plex Mono** — Copyright 2019 IBM Corp. Licensed under the SIL Open Font
License, Version 1.1 (OFL-1.1). The fonts are compiled into the application so that no font server
is contacted. License text: <https://openfontlicense.org/>; packaged by the Fontsource project
(`@fontsource-variable/ibm-plex-sans`, `@fontsource/ibm-plex-mono`), whose own `LICENSE` files ship
in the source tree.

**Lucide** — Copyright (c) 2026 Lucide Icons and Contributors, ISC License. Icons derived from
Feather (Copyright (c) 2013-2023 Cole Bemis, MIT License).

**Cytoscape.js** — Copyright (c) The Cytoscape Consortium, MIT License. Used for the relationship
graph canvas.

**Next.js, React and their runtime dependencies** — MIT License (Vercel, Inc.; Meta Platforms, Inc.
and affiliates).

## Bundled in the API and collector images

**psycopg 3** — LGPL-3.0-only (The Psycopg Team). Used unmodified as the PostgreSQL driver.
Source: <https://psycopg.org/>. Anyone redistributing these images must keep this notice and allow
the library to be replaced, as the LGPL requires.

**pypdfium2 / PDFium** — BSD-3-Clause and Apache-2.0 (pypdfium2 team; PDFium: Copyright 2014 The
PDFium Authors). Used for rendering PDF pages during optional OCR.

**pypdf** — BSD-3-Clause (Mathieu Fenniak and contributors). **FastAPI, SQLAlchemy, Pydantic,
Alembic, Celery, uvicorn, httpx2, cryptography, argon2-cffi, redis-py** — MIT, BSD-3-Clause or
Apache-2.0; see the inventory. **defusedxml** — Python Software Foundation License.

## Bundled in the discovery-runner image

**Subfinder 2.16.0** — Copyright (c) ProjectDiscovery, MIT License. The published release binary is
downloaded during the image build, verified against a pinned SHA-256 and copied together with its
`LICENSE.md`, which is available in the image at
`/usr/local/share/licenses/subfinder/LICENSE.md`. Source: <https://github.com/projectdiscovery/subfinder>.

## Bundled in the collector image

**Sherlock (`sherlock-project`)** — MIT License (Sherlock Project). Executed as a separate process
for username discovery. Its transitive dependency **stem** is LGPLv3 (The Tor Project) and is used
unmodified.

## Optional, built only with `TRACEHOLLOW_INSTALL_OCR=true`

**Tesseract OCR** with English and Turkish language data — Apache License 2.0, from Debian
packages.

## Base images (pulled, not redistributed by this project)

- `python:3.13.15-slim-trixie` — Python Software Foundation License and Debian package licenses.
- `node:24.21.0-trixie-slim` — MIT (Node.js) and Debian package licenses.
- `pgvector/pgvector:0.8.6-pg18-trixie` — PostgreSQL License (PostgreSQL and pgvector).
- `redis:8.10.1-trixie` — Redis 8 is licensed by its vendor under AGPLv3, RSALv2 or SSPLv1. It is
  used as an unmodified upstream image for the local broker; this project neither modifies nor
  redistributes it, and offers no Redis service to third parties.
- `ghcr.io/astral-sh/uv` — Apache-2.0 or MIT, used only during the build.

## Not bundled

No language model ships with Tracehollow. Optional local answering uses Ollama (MIT) with models the
operator downloads; each model carries its own license. Collected material and any provider's terms
of service are separate from these code licenses.
