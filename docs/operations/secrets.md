# Secrets: generation, backup and rotation

`scripts/setup.sh` creates every secret that is missing in `secrets/` using `openssl rand` (or
`/dev/urandom`). It never overwrites a non-empty secret. Files are mounted into containers through
Compose `secrets:` and read via `TRACEHOLLOW_*_FILE` settings.

| File | Purpose | Where it is also stored |
| --- | --- | --- |
| `postgres_superuser_password` | `postgres` superuser | Inside the database (set at first initialisation) |
| `postgres_app_password` | `tracehollow_app` role used by api, worker, migrate | Inside the database (set at first initialisation) |
| `redis_password` | Redis `default` user | `redis_users.acl` holds its SHA-256 digest |
| `redis_users.acl` | Redis ACL file, derived by `setup.sh` | — |
| `app_secret_key` | HMAC key for CSRF tokens | — |
| `bootstrap_token` | One-time web setup | — |

## Backup

Copy `secrets/` to protected storage separately from database backups, and again after any rotation.
Anyone holding both a database backup and `secrets/` can operate the installation.

## Rotation

These procedures change live credentials. Test them on a non-production project first
(for example `COMPOSE_PROJECT_NAME=tracehollow-rotation-test`). Replace a value atomically with a new
random string (a trailing newline is ignored):

```bash
openssl rand -hex 32 > secrets/<name>.new && chmod 0644 secrets/<name>.new && mv secrets/<name>.new secrets/<name>
```

### `app_secret_key`

Replace the file, then `docker compose up --detach --force-recreate api`. Existing CSRF tokens become
invalid; open pages must be reloaded. Sessions remain valid (session tokens do not depend on this key).
To sign everyone out, use `reset-password` for each user.

### `redis_password`

1. Replace `secrets/redis_password` and run `scripts/setup.sh` (it re-derives `redis_users.acl`).
2. `docker compose up --detach --force-recreate redis api worker`

### `postgres_app_password`

PostgreSQL keeps the password in the database, so change it there first:

```bash
docker compose exec postgres psql -U postgres -c '\password tracehollow_app'
```

Enter the new value (for example generated with `openssl rand -hex 32`), write the same value to
`secrets/postgres_app_password`, then `docker compose up --detach --force-recreate migrate api worker`.

### `postgres_superuser_password`

Change it with `docker compose exec postgres psql -U postgres -c '\password postgres'` and store the
same value in `secrets/postgres_superuser_password`. The file is only read when a new data volume is
initialised, but keeping it accurate avoids surprises.

### `bootstrap_token`

It is only accepted while no administrator exists. To rotate before setup, replace the file and
recreate `api`. After setup the value is inert.

## If a secret leaked

1. Rotate the affected secret as above.
2. If the administrator password may be known, run
   `docker compose exec api python -m app.cli reset-password --username <name>`; this revokes all of
   that user's sessions.
3. Review `docker compose logs api` for unexpected sign-ins (`session_created` events).

Status: these rotation procedures are documented but not yet exercised by automated tests.
