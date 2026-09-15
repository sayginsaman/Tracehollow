#!/bin/sh
# Runs once, when the PostgreSQL data volume is initialised for the first time.
# Creates the non-superuser role used by the API, worker and migrations and makes it the
# owner of the application database. The password is read inside psql from the mounted
# secret file, so it never appears in process arguments or the environment.
set -eu

: "${POSTGRES_DB:?}"
: "${POSTGRES_USER:?}"
: "${TRACEHOLLOW_APP_DB_USER:?}"

psql --no-psqlrc -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_user="$TRACEHOLLOW_APP_DB_USER" \
  --set=app_db="$POSTGRES_DB" <<'SQL'
\set app_password `cat /run/secrets/postgres_app_password`
CREATE ROLE :"app_user" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  PASSWORD :'app_password';
ALTER DATABASE :"app_db" OWNER TO :"app_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
SQL
