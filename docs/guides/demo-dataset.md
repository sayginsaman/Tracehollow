# Recreate the demonstration dataset

The screenshots in the README and in these guides come from one synthetic case about a fictional
logistics company. This page shows how to build the same case on your own machine, so you can click
through what the screenshots show.

Everything in the dataset is invented. Aurora Freight and Northwind Logistics are fictional,
`aurora-freight.example` is a reserved example domain, `203.0.113.42` is a documentation address,
and the pages and announcement feed are served by a fixture container that runs beside the stack.
No real source is contacted and no real person appears.

## When to use this

Use it to evaluate Tracehollow, to prepare a demonstration, or to check a change against a dataset
that is the same every time. Do not use it on an installation that holds real investigations: run
it against a disposable Compose project, as shown below.

## What you get

| In the case | Contents |
| --- | --- |
| Accounts | `demo.admin` (administrator), `demo.analyst` (analyst), `demo.viewer` (viewer, member of the case) |
| Case | "Aurora Freight infrastructure review (synthetic demo)", tagged `synthetic-demo` |
| Evidence | A registry extract, a WHOIS-style JSON record, a generated PDF, a collected press page and two collections of the announcement feed |
| Entities and relationships | Seven entities (organizations, a domain, a subdomain, an address, an email, a forum account) and five relationships, all unreviewed analyst assertions |
| Collection | Two saved queries and three runs, including a baseline and a second run that finds one new and one changed announcement |
| Monitoring | One monitor, created paused, with a case budget and a change report |
| AI | One question answered by your local model, with citations into the evidence |

## Prerequisites

- The repository checked out, and Docker Compose working (see
  [first-investigation.md](first-investigation.md)).
- Ports 3200 and 8200 free on `127.0.0.1`. The demo runs as its own Compose project so it cannot
  disturb an existing installation.
- Optional: [Ollama](https://ollama.com) with the models from
  [../operations/ai-models.md](../operations/ai-models.md). Without it, pass `--skip-ai`.

## Build the dataset

```bash
export COMPOSE_PROJECT_NAME=tracehollow-demo
export COMPOSE_FILE=compose.yaml:compose.demo.yaml
export TRACEHOLLOW_WEB_PORT=3200 TRACEHOLLOW_API_PORT=8200

docker compose up --build --detach --wait

TRACEHOLLOW_DEMO_PASSWORD='choose at least 12 characters' \
  python3 scripts/seed_demo.py --web-url http://localhost:3200
```

Choose your own password; it is used for all three demo accounts and is never stored in the
repository. The script prints each step as it runs and takes a few minutes, most of it waiting for
the local model to answer.

Open <http://localhost:3200> and sign in as `demo.analyst`.

| Option | Effect |
| --- | --- |
| `--skip-ai` | Leaves out the AI conversation. Use it when no local model is configured. |
| `--skip-pdf` | Leaves out the generated PDF import. |
| `--reset` | Deletes an existing demo case first, then seeds again. |
| `--ai-timeout` | Seconds to wait for the model (default 900; the first answer after a cold start is the slow one). |

## What the script does, and what it refuses to do

The script talks to the same HTTP API the interface uses, so every record it creates goes through
the same validation, authorization and provenance rules as your own work. It does not write to the
database directly and it cannot bypass an invariant.

Two safety properties are worth knowing:

- **The monitor is left paused.** Enabling it would start scheduled collection, so the script never
  does that for you. Its two runs are triggered explicitly, and the change report you see comes from
  comparing them.
- **External notifications stay off.** The webhook adapter is disabled by default and the script
  does not enable it, so seeding sends nothing anywhere.

The controlled change is produced by switching the fixture container's feed from its first version
to its second between the two runs: one announcement is renamed and one is added. That is why the
monitor's second occurrence reports one changed and one new item.

![A change report listing the changed and new announcements with links to the evidence on both sides](../screenshots/08-change-detail.jpg)

## Clean up

Remove the demo stack and all of its data:

```bash
COMPOSE_PROJECT_NAME=tracehollow-demo COMPOSE_FILE=compose.yaml:compose.demo.yaml \
  docker compose down --volumes
```

This deletes only the demo project's containers and volumes. Your own installation, which runs
under a different project name, is untouched.
