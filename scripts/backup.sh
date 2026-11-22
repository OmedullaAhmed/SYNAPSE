#!/bin/sh
# Back up the StockFlow database:  scripts/backup.sh <postgres-url> <folder>   (PGPASSWORD in the environment)
# Writes stockflow_YYYYmmdd_HHMMSS.dump (pg_dump custom format, compressed), checks it can be read, deletes ones older than BACKUP_DAYS.
set -eu
URL="$1"; DIR="$2"; DAYS="${BACKUP_DAYS:-30}"
mkdir -p "$DIR"
F="$DIR/stockflow_$(date +%Y%m%d_%H%M%S).dump"
pg_dump --format=custom --compress=6 --no-owner --file="$F.part" "$URL"
pg_restore --list "$F.part" > /dev/null          # a backup that cannot be read is not a backup
mv "$F.part" "$F"
find "$DIR" -name 'stockflow_*.dump' -mtime +"$DAYS" -delete
echo "backup ok: $F ($(du -h "$F" | cut -f1))"
