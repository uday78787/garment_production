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

case "${RESET_DEMO_DATA_ON_STARTUP:-false}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn])
        case "${DEBUG:-false}" in
            1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) ;;
            *)
                echo "RESET_DEMO_DATA_ON_STARTUP requires DEBUG=True; refusing to clear the database." >&2
                exit 1
                ;;
        esac
        echo "WARNING: resetting database data and rebuilding the demo factory..."
        python manage.py setup_demo_factory
        ;;
    0|[Ff][Aa][Ll][Ss][Ee]|[Nn][Oo]|"")
        echo "Setting up roles/groups..."
        python manage.py setup_roles
        ;;
    *)
        echo "RESET_DEMO_DATA_ON_STARTUP must be a boolean value." >&2
        exit 1
        ;;
esac

echo "Collecting static files..."
python manage.py collectstatic --noinput

exec "$@"
