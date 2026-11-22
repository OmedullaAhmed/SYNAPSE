#!/bin/sh
# Restore a StockFlow backup into an EMPTY database:  scripts/restore.sh <backup.dump> <postgres-url-of-empty-db>
# Then point DATABASE_URL at it. Test this regularly on a spare database: an untested backup is a hope, not a plan.
set -eu
pg_restore --no-owner --exit-on-error --dbname="$2" "$1"
psql "$2" -Atc "select 'plans', count(*) from plans union all select 'audit', count(*) from audit union all select 'users', count(*) from users"
