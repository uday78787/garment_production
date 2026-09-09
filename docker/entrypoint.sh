#!/bin/sh
set -e

# Wait for the database to be reachable (only meaningful when DATABASE_URL
# points at Postgres; harmless no-op check otherwise).
if [ -n "$DATABASE_URL" ]; then
    echo "Waiting for database..."
    python - <<'PYEOF'
import os
import sys
import time

import environ

env = environ.Env()
db_config = env.db("DATABASE_URL", default="")
if db_config.get("ENGINE", "").endswith("sqlite3"):
    sys.exit(0)

import psycopg2

host = db_config.get("HOST")
port = db_config.get("PORT") or 5432
user = db_config.get("USER")
password = db_config.get("PASSWORD")
name = db_config.get("NAME")

for attempt in range(30):
    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=name
        )
        conn.close()
        print("Database is up.")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"Database not ready yet ({exc}); retrying...")
        time.sleep(2)
else:
    print("Database never became available.")
    sys.exit(1)
PYEOF
fi

echo "Applying migrations..."
python manage.py migrate --noinput

echo "Setting up roles/groups..."
python manage.py setup_roles

echo "Collecting static files..."
python manage.py collectstatic --noinput

exec "$@"
