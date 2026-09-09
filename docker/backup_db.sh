#!/bin/sh
# docker/backup_db.sh
#
# Dumps the Postgres database from the running `db` container into
# docker/backups/, timestamped. Intended to be run from the host, e.g. via
# cron:
#   0 2 * * * cd /path/to/project && ./docker/backup_db.sh
#
# Restore with:
#   docker compose exec -T db psql -U <user> <db> < docker/backups/<file>.sql

set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$(dirname "$0")/backups"
mkdir -p "$BACKUP_DIR"

POSTGRES_USER="${POSTGRES_USER:-garment_user}"
POSTGRES_DB="${POSTGRES_DB:-garment_production}"

echo "Backing up $POSTGRES_DB to $BACKUP_DIR/${POSTGRES_DB}_${TIMESTAMP}.sql ..."

docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
    > "$BACKUP_DIR/${POSTGRES_DB}_${TIMESTAMP}.sql"

echo "Backup complete."

# Keep only the last 14 backups
ls -1t "$BACKUP_DIR"/*.sql 2>/dev/null | tail -n +15 | xargs -r rm --
