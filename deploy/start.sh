#!/bin/sh
set -eu

# Bootstrap failures stop startup rather than leaving an unmigrated API running.
gateway wait-database --timeout 30
case "${GATEWAY_RUN_MIGRATIONS:-true}" in
  true)
    if ! alembic upgrade head; then
      echo "Database migration failed; check database readiness and GATEWAY_ configuration." >&2
      exit 1
    fi
    ;;
  false) ;;
  *)
    echo "GATEWAY_RUN_MIGRATIONS must be true or false." >&2
    exit 1
    ;;
esac
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --no-access-log
