# Troubleshooting

Symptoms an operator actually meets, and what to do about each. Every command here runs from the
repository directory, against the Compose project you started.

## First: three commands that answer most questions

```bash
docker compose ps                       # which services are up, healthy or exited
docker compose logs --tail 100 api      # or worker, collector, dispatcher, web, migrate
curl -s http://localhost:8000/api/health/ready
```

A ready installation answers:

```json
{"status":"ready","checks":{"database":"ok","migrations":"ok","redis":"ok","storage":"ok"}}
```

Anything other than `ok` names the subsystem to look at. The same information is in the interface
under **Configuration → Environment status**, together with worker health.

## The stack will not start

### `docker compose up --wait` never returns, or a service stays unhealthy

Find the unhealthy service with `docker compose ps`, then read its log. The usual causes:

| Service | Typical cause |
| --- | --- |
| `postgres` | The data volume belongs to a newer PostgreSQL than the image. Restore from a backup rather than downgrading. |
| `migrate` | A migration failed; the log names it. The job must reach `completed` before `api` starts. |
| `api` | `check-config` rejected the configuration, or a secret file is missing. |
| `worker`, `collector`, `ai-worker` | Cannot reach Redis, or the broker password changed without recreating the service. |
| `discovery-gateway` | Its allowlist or certificate verification rejected the configured providers. |

Validate the configuration without starting anything else:

```bash
docker compose run --rm api python -m app.cli check-config
```

### `bind: address already in use`

Something else holds port 3000 or 8000. Either stop it, or run Tracehollow on other ports:

```bash
TRACEHOLLOW_WEB_PORT=3200 TRACEHOLLOW_API_PORT=8200 docker compose up --detach --wait
```

Set the same values in `.env` if you want them to persist. The services bind to `127.0.0.1` unless
you change `TRACEHOLLOW_WEB_BIND_ADDRESS` or `TRACEHOLLOW_API_BIND_ADDRESS`.

### The migration job failed and the API will not start

Read the failure, fix the cause, then run the job again:

```bash
docker compose logs migrate
docker compose up migrate
docker compose up --detach --wait
```

Migrations are forward-only. There is no tested downgrade path: to go back to an earlier version,
restore the backup you took before upgrading
([backup-restore.md](backup-restore.md)).

## I cannot sign in

### The setup page asks for a token I do not have

```bash
cat secrets/bootstrap_token
```

The token is accepted only while no administrator exists. After the first administrator is created
the value is inert; see [secrets.md](secrets.md) to rotate it before setup.

### I forgot the administrator password

```bash
docker compose exec api python -m app.cli reset-password --username <name>
```

This sets a new password and revokes every session that user holds.

### Sign-in fails with a request-forgery or origin error

You are reaching the interface under a hostname the installation does not trust. Add it to
`TRACEHOLLOW_PUBLIC_ORIGIN`, `TRACEHOLLOW_TRUSTED_ORIGINS` and `TRACEHOLLOW_WEB_ALLOWED_HOSTS`,
then recreate `api` and `web`. Localhost deployment still requires authentication; do not relax
these settings to make a proxy work.

## Work does not finish

### A run stays queued

Runs are dispatched from PostgreSQL by the `dispatcher` and executed by `collector` (collection) or
`worker` (imports and documents). Check that both are healthy:

```bash
docker compose ps dispatcher collector worker
docker compose logs --tail 50 dispatcher
```

If the broker was restarted, recreate the workers so they reconnect:

```bash
docker compose up --detach --force-recreate worker collector ai-worker
```

Queued work survives a restart: it lives in the database, not in Redis.

### A run finished as `partial` or `failed`

That is a result, not a fault. Open the run: each source reports its own outcome, and the page
distinguishes no findings, incomplete coverage, authentication problems, rate limits and source
failures. `no_findings` is recorded only when a source answered and had nothing;
[../connectors/README.md](../connectors/README.md) lists what each outcome means per connector.

### An import is stuck in processing

Open **Imports → Processing jobs**. A job may be waiting for an answer from you, for example the
date order of a WhatsApp export. Jobs can be cancelled and processed again; finished pages are
kept. See [../imports/whatsapp.md](../imports/whatsapp.md).

## The monitor never runs

Monitors are created paused, on purpose. To start one:

1. Open the monitor and tick the confirmation that it will contact its sources on the schedule.
   The API refuses `enable` without it (`422 recurring_collection_not_acknowledged`).
2. Check the schedule against the installation's shortest interval
   (`TRACEHOLLOW_MONITOR_MIN_INTERVAL_MINUTES`, default 60). Shorter schedules are refused.
3. Check the budget. A monitor whose case or monitor budget is used up records the slot and does
   not collect; the occurrence says so.

After a restore every monitor is paused again, deliberately, so a restored installation cannot
resume collecting without a person deciding to. See [retention.md](retention.md) and
[backup-restore.md](backup-restore.md).

## AI answers are unavailable

Check **Configuration → Environment status** first; it reports whether the model host answers.

| Symptom | Cause and fix |
| --- | --- |
| AI menu missing or disabled | `TRACEHOLLOW_AI_ENABLED` is false, or AI is turned off for this case in **Case settings**. |
| "The model host did not answer" | Ollama is not running, or not reachable from containers. The default is `http://host.docker.internal:11434`; on Linux set `TRACEHOLLOW_AI_OLLAMA_BASE_URL` to an address the containers can reach. |
| "Model not found" | Pull the models named in [ai-models.md](ai-models.md). |
| The first answer takes minutes | Expected on a cold start: the model is being loaded. Later answers are much faster. |
| A cloud request is refused for a case | The case is local-only. Nothing falls back from local to cloud; change the case's data policy deliberately or keep it local. |

## Evidence problems

### A download reports that the hash does not match

Do not treat the file as evidence. Reconcile the store, which recovers interrupted writes and
verifies hashes:

```bash
docker compose exec api python -m app.cli reconcile-evidence
```

[evidence-storage.md](evidence-storage.md) explains what the command checks and what it reports.

### Evidence files are missing after moving the installation

The evidence volume must move with the database. A database restored without its evidence files
leaves records whose bytes are gone; [backup-restore.md](backup-restore.md) describes the
consistent procedure for both.

## Disk and resources

```bash
docker system df
docker compose exec postgres df -h /var/lib/postgresql/data
```

Collection, imports and OCR all write to the evidence volume. Retention is off by default; turn it
on deliberately, with a preview, as described in [retention.md](retention.md). Removing a case
deletes its evidence files as part of an observable, retryable job.

## Starting again from scratch

This destroys the database, the broker state and every stored evidence file for that project:

```bash
docker compose down --volumes
```

Take a backup first if there is anything you need
([backup-restore.md](backup-restore.md)).

## Still stuck

Collect the facts before asking for help: `docker compose ps`, the last 100 log lines of the
failing service, the output of `/api/health/ready`, and the exact steps that produced the problem.
Logs are written without secrets or evidence text, so they are safe to share; check anything you
paste all the same.
