#!/bin/sh
set -e

DB_WAIT_HOST="${DB_HOST:-}"
DB_WAIT_PORT="${DB_PORT:-3306}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

if [ -z "$DB_WAIT_HOST" ] && [ -n "${DATABASE_URL:-}" ]; then
    DB_WAIT_HOST=$(printf "%s" "$DATABASE_URL" | sed -nE 's|^[a-zA-Z0-9+]+://[^@]+@([^:/?]+).*|\1|p')
    DB_WAIT_PORT_FROM_URL=$(printf "%s" "$DATABASE_URL" | sed -nE 's|^[a-zA-Z0-9+]+://[^@]+@[^:/?]+:([0-9]+).*|\1|p')
    if [ -n "$DB_WAIT_PORT_FROM_URL" ]; then
        DB_WAIT_PORT="$DB_WAIT_PORT_FROM_URL"
    fi
fi

# Wait for database to be ready when host is provided
if [ -n "$DB_WAIT_HOST" ]; then
    echo "Waiting for database at $DB_WAIT_HOST:$DB_WAIT_PORT..."
    until nc -z "$DB_WAIT_HOST" "$DB_WAIT_PORT"; do
        echo "Database not ready, sleeping 3s..."
        sleep 3
    done
    echo "Database is reachable."
else
    echo "No DB host found in DB_HOST or DATABASE_URL. Skipping DB wait."
fi

# Ensure migrations path exists and is writable for first-time boot.
mkdir -p /app/migrations

if [ "$RUN_MIGRATIONS" = "true" ]; then
    if find /app/migrations -type f -name "*.py" ! -name "__init__.py" -print -quit | grep -q .; then
        echo "Applying Aerich migrations..."
        if ! aerich upgrade; then
            echo "aerich upgrade failed, retrying with --fake."
            aerich upgrade --fake || true
        fi
    else
        echo "No migration files found. Attempting aerich init-db..."
        aerich init-db || echo "aerich init-db skipped (already initialized or not required)."
    fi
else
    echo "RUN_MIGRATIONS=false. Skipping Aerich migration step."
fi

# Start FastAPI
PORT="${PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-3}"
echo "Starting FastAPI on port $PORT with $WORKERS workers..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS"
