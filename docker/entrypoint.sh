#!/usr/bin/env bash
# Entrypoint for the lean Plane CE Railway image.
#
# It prepares the environment, then runs the upstream /app/start.sh (which
# validates variables, writes /app/plane.env and starts supervisord). This
# script stays PID 1 so Railway's SIGTERM reaches supervisord and every Plane
# process shuts down cleanly.
set -euo pipefail

log() { echo "[plane-railway] $*"; }
die() { echo "[plane-railway] ERROR: $*" >&2; exit 1; }

PLANE_ENV_FILE=/app/plane.env

# Write KEY=VALUE into /app/plane.env. start.sh exports that file after our
# environment, so keys that it does not manage itself (GUNICORN_WORKERS, ...)
# must be changed there to take effect.
set_plane_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$PLANE_ENV_FILE"; then
    K="$key" V="$value" awk 'index($0, ENVIRON["K"] "=") == 1 { print ENVIRON["K"] "=" ENVIRON["V"]; next } { print }' \
      "$PLANE_ENV_FILE" > "${PLANE_ENV_FILE}.tmp" && mv "${PLANE_ENV_FILE}.tmp" "$PLANE_ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$PLANE_ENV_FILE"
  fi
}

# Print $1 if it is an integer in [$2, $3], otherwise print the default $4.
int_in_range() {
  local v="$1" lo="$2" hi="$3" def="$4"
  if [[ "$v" =~ ^[0-9]+$ ]] && (( v >= lo && v <= hi )); then echo "$v"; else echo "$def"; fi
}

# ---------------------------------------------------------------- domain ----
# DOMAIN_NAME is the host users open. On Railway it defaults to the generated
# public domain; set it to your custom domain once that is attached.
DOMAIN_NAME="${DOMAIN_NAME:-${RAILWAY_PUBLIC_DOMAIN:-}}"
DOMAIN_NAME="${DOMAIN_NAME#http://}"
DOMAIN_NAME="${DOMAIN_NAME#https://}"
DOMAIN_NAME="${DOMAIN_NAME%%/*}"
[ -n "$DOMAIN_NAME" ] || die "DOMAIN_NAME is empty. In Railway, generate a public domain for this service (Settings > Networking) or set DOMAIN_NAME."
export DOMAIN_NAME
export APP_PROTOCOL="${APP_PROTOCOL:-https}"

# ---------------------------------------------------------------- broker ----
# Plane's Celery broker URL comes from AMQP_URL. Celery accepts a redis:// URL
# there too, so by default we reuse the Redis service on a separate database
# number (DB 0 is flushed by Plane's clear_cache on every API start).
[ -n "${REDIS_URL:-}" ] || die "REDIS_URL is empty. Reference it from the Redis service: \${{Redis.REDIS_URL}}"
if [ -z "${AMQP_URL:-}" ]; then
  broker_db="$(int_in_range "${PLANE_BROKER_REDIS_DB:-1}" 1 15 1)"
  AMQP_URL="$(python3 - "$REDIS_URL" "$broker_db" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
url, db = sys.argv[1], sys.argv[2]
parts = urlsplit(url)
if parts.scheme not in ("redis", "rediss") or not parts.hostname:
    sys.exit("REDIS_URL must look like redis://user:password@host:port")
print(urlunsplit((parts.scheme, parts.netloc, "/" + db, parts.query, "")))
PY
)" || die "could not derive the Celery broker URL from REDIS_URL"
  export AMQP_URL
  log "Celery broker: Redis database ${broker_db} (no RabbitMQ)"
else
  log "Celery broker: AMQP_URL provided explicitly"
fi

# ------------------------------------------------------------ S3 / bucket ---
# Railway buckets use virtual-hosted style URLs. Plane does not expose an
# addressing-style setting, but botocore reads it from the AWS config file.
case "${PLANE_S3_ADDRESSING_STYLE:-virtual}" in
  virtual|path|auto) s3_style="${PLANE_S3_ADDRESSING_STYLE:-virtual}" ;;
  *) log "ignoring invalid PLANE_S3_ADDRESSING_STYLE='${PLANE_S3_ADDRESSING_STYLE}', using virtual"; s3_style=virtual ;;
esac
cat > /app/aws-config <<EOF
[default]
s3 =
    addressing_style = ${s3_style}
EOF
export AWS_CONFIG_FILE=/app/aws-config

if [ -n "${AWS_S3_BUCKET_NAME:-}" ] && [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
  python3 /opt/plane-railway/bucket_cors.py || log "bucket CORS step failed (non-fatal)"
fi

# --------------------------------------------------- process sizing ---------
# Celery's default pool size is the host CPU count, which on Railway can mean
# dozens of processes and gigabytes of RAM. Keep it small and explicit.
export PLANE_WORKER_CONCURRENCY="$(int_in_range "${PLANE_WORKER_CONCURRENCY:-2}" 1 16 2)"
export PLANE_WORKER_MAX_MEMORY_KB="$(int_in_range "${PLANE_WORKER_MAX_MEMORY_KB:-393216}" 65536 4194304 393216)"
gunicorn_workers="$(int_in_range "${PLANE_GUNICORN_WORKERS:-1}" 1 8 1)"
set_plane_env GUNICORN_WORKERS "$gunicorn_workers"
log "sizing: gunicorn workers=${gunicorn_workers}, celery concurrency=${PLANE_WORKER_CONCURRENCY}"

# ------------------------------------------------------------------ email ---
if [ -n "${MAIL_API_KEY:-}" ]; then
  log "email: SMTP bridge on 127.0.0.1:${MAIL_BRIDGE_PORT:-2525} -> ${MAIL_PROVIDER:-resend} HTTPS API"
else
  log "email: MAIL_API_KEY not set, outgoing emails are logged and dropped (see README > Email)"
fi

# ------------------------------------------------------------------- run ----
rm -f /tmp/plane-fatal /tmp/supervisord.pid
cd /app

stopping=0
child=""
on_term() {
  stopping=1
  log "stop signal received, stopping all Plane processes"
  if [ -s /tmp/supervisord.pid ]; then
    kill -TERM "$(cat /tmp/supervisord.pid)" 2>/dev/null || true
  elif [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null || true
  fi
}
trap on_term TERM INT

set +e
/app/start.sh &
child=$!
rc=0
while :; do
  wait "$child"
  rc=$?
  kill -0 "$child" 2>/dev/null || break
done

if [ -e /tmp/plane-fatal ]; then
  log "a Plane process failed repeatedly; exiting so Railway restarts the container"
  exit 1
fi
if [ "$stopping" = 1 ]; then
  exit 0
fi
# supervisord should never exit on its own; make Railway's ON_FAILURE policy restart us.
[ "$rc" -eq 0 ] && rc=1
exit "$rc"
