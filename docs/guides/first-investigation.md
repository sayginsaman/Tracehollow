# Install Tracehollow and run your first investigation

This tutorial takes you from an empty machine to a case that holds evidence with provenance, an
entity, a relationship and a report you can hand over. It uses only material Tracehollow generates
itself, so you contact no real source and need no credentials.

You need about 30 minutes, most of it waiting for images to build.

When you finish you will have seen the four things Tracehollow is built around: evidence keeps its
origin, runs keep their outcome, interpretation is recorded separately from observation, and
nothing claims more than the source supports.

## Before you start

| You need | Why |
| --- | --- |
| Docker Engine or Docker Desktop with Compose v2 | Runs the whole stack |
| `bash` and either `openssl` or `/dev/urandom` | Generates the local secrets |
| About 6 GB of free disk space | Images, database and evidence volumes |
| Ports 3000 and 8000 free on `127.0.0.1` | The web interface and the API |

You do not need a cloud account, a paid API or a language model. The AI features are optional and
are not part of this tutorial; [operations/ai-models.md](../operations/ai-models.md) covers them.

## 1. Start the stack

```bash
git clone https://github.com/sayginsaman/Tracehollow.git
cd Tracehollow
scripts/setup.sh
docker compose up --build --detach --wait
```

`scripts/setup.sh` writes `.env` and generates secrets under `secrets/`. It never overwrites an
existing secret, so it is safe to run again.

The last command returns once every service reports healthy. The first build takes several minutes.

Check what is running:

```bash
docker compose ps
```

You should see `postgres`, `redis`, `api`, `worker`, `dispatcher`, `collector`,
`discovery-runner`, `discovery-gateway`, `ai-worker` and `web` up, and the one-shot `migrate` and
`db-extensions` jobs completed.

## 2. Create the administrator

Print the one-time setup token:

```bash
cat secrets/bootstrap_token
```

Open <http://localhost:3000>. Tracehollow redirects you to **Create the administrator**. Paste the
token, choose a username, and choose a password of at least 12 characters. Keep it somewhere safe:
there is no password reset by email, and the token works only once.

Sign in. You land on the **Overview**, which shows work that needs a decision, recent cases, recent
runs and imports, and whether the services Tracehollow depends on respond.

## 3. Open a case

Press **New case** and fill in:

- **Title**: `Tutorial case`
- **Purpose**: why you are looking. Everything in the case is judged against this later.
- **Scope**: what you may collect and what you may not.
- **Tags**: `tutorial`, for example.

Press **Create case**. You are now inside the case, and the sidebar shows the case sections:
Collect (Queries & runs, Imports), Examine (Evidence, Entities, Relationships, Graph), Analyze
(Timeline, Compare, AI) and Report (Reports, Case settings).

## 4. Import a piece of evidence

Real investigations start with material you already have. Create a small file:

```bash
cat > /tmp/tutorial-note.txt <<'TXT'
Registry extract (tutorial data, not a real registry response)

Domain:      tutorial-target.example
Registrant:  Tutorial Holdings Ltd
Registered:  2024-11-03
Contact:     press@tutorial-target.example
TXT
```

In the case, open **Imports → Text or JSON**, choose the file, and fill in **Import origin** with
where the material came from, for example `Written by hand for the Tracehollow tutorial`. The
import origin is required: Tracehollow will not store evidence whose origin nobody recorded.

Press **Import**. Open **Evidence** and select the new record. You see:

- a provenance panel: acquisition (`Authorized import`), your import origin, when it was imported;
- the SHA-256 of the stored bytes and when integrity was last checked;
- the content as inert text with line numbers. Imported HTML and scripts are never rendered.

![An evidence record with its provenance panel, hash and line-numbered content](../screenshots/03-evidence-provenance.jpg)

## 5. Run a collection

Collection happens through saved queries. In this tutorial you use the synthetic fixture connector,
which generates deterministic data and contacts nothing.

1. Open **Queries & runs → New query**.
2. Input type `username`, input value `tutorial-user`.
3. Choose the source **Synthetic fixture (not a real source)**.
4. Leave the scenario on `findings`. The other scenarios (`no_findings`, `partial`, `failure`,
   `flaky`, `rate_limited`, `authentication_required`, `access_denied`) let you see how each outcome
   is reported without breaking anything.
5. Save the query and press **Run**.

Open the run when it finishes. The run page names the outcome per source, the number of items, the
pages fetched and how long it took. This matters more than it looks: `no_findings` is only ever
recorded when a source answered and had nothing, and a failure never silently becomes an absence.

Evidence collected by the run appears under **Evidence**, labelled `Collected` rather than
`Authorized import`.

## 6. Record what you think it means

Observation and interpretation are kept apart on purpose.

1. Open **Entities → New entity**. Create an organization named `Tutorial Holdings Ltd`.
2. Create a second entity, a domain `tutorial-target.example`.
3. Open **Relationships → New relationship**: `Tutorial Holdings Ltd` **owns**
   `tutorial-target.example`. Choose the evidence record from step 4 as supporting evidence and
   leave the review status unreviewed.
4. On the case overview, add a note explaining what you have and what you have not established.

Open **Graph**. Both entities appear, linked by the relationship, drawn as an analyst assertion
rather than an observation. Selecting a node shows where it came from.

![The relationship graph with an entity inspector showing its origin and relationship count](../screenshots/04-relationship-graph.jpg)

## 7. Produce a report

Open **Reports**. Nothing is included unless you select it. Tick the two entities, the relationship
and the evidence excerpt, then press **Preview report**.

The preview is the exact file you would download. It contains no scripts and loads nothing when
opened; citations point to excerpts bundled inside the file, so they still resolve offline. If you
are handing it to someone who should not see certain values, use the redaction box before
previewing.

Press **Download HTML** and open the file in a browser to confirm.

## What you have learned

- Evidence carries its origin, its hash and its collection dates, and is shown inert.
- Runs report explicit outcomes per source; absence is only recorded when it was observed.
- Entities and relationships are your assertions until an analyst reviews them.
- Reports contain exactly what you chose and stay verifiable offline.

## Where to go next

| Next | Guide |
| --- | --- |
| Collect from real public sources | [../connectors/README.md](../connectors/README.md) |
| Import an authorized WhatsApp export or a PDF | [../imports/whatsapp.md](../imports/whatsapp.md), [../operations/document-processing.md](../operations/document-processing.md) |
| Ask questions about the case with a local model | [../operations/ai-models.md](../operations/ai-models.md) |
| Watch a source for changes | [../monitoring/README.md](../monitoring/README.md) |
| Add colleagues with roles and case access | [../security/permissions.md](../security/permissions.md) |
| Load the richer demonstration dataset from the screenshots | [demo-dataset.md](demo-dataset.md) |

## Clean up

Stop the stack but keep the data:

```bash
docker compose down
```

Start it again later with `docker compose up --detach`. To remove the tutorial data as well,
including the database and every stored evidence file:

```bash
docker compose down --volumes
```
