# Plane Community Edition (lean) for Railway.
#
# Thin layer on top of Plane's official all-in-one community image. It does not
# rebuild Plane. It only adds:
#   - an entrypoint that derives Railway settings, forwards stop signals and
#     uses Redis as the Celery broker (no RabbitMQ service needed)
#   - a supervisor config that logs to stdout (Railway log viewer) and caps the
#     Celery worker pool (the upstream default is one process per host CPU)
#   - automatic CORS setup for the Railway bucket (browser uploads)
#   - a local SMTP -> HTTPS email API bridge (Railway Hobby blocks SMTP)
#
# Upgrade: change the tag AND digest below together (see README "Upgrading").
FROM makeplane/plane-aio-community:v1.5.0@sha256:16a2680cc7ab0c1ef86396905563816246692e32302154cb2a4f335276ac03b0

LABEL org.opencontainers.image.title="plane-ce-railway" \
      org.opencontainers.image.description="Unofficial lean Railway packaging of Plane Community Edition (AGPL-3.0, makeplane/plane)" \
      org.opencontainers.image.licenses="MIT AND AGPL-3.0-only"

ENV PYTHONUNBUFFERED=1 \
    PLANE_WORKER_CONCURRENCY=2 \
    PLANE_WORKER_MAX_MEMORY_KB=393216 \
    PLANE_GUNICORN_WORKERS=1 \
    PLANE_BROKER_REDIS_DB=1 \
    PLANE_S3_ADDRESSING_STYLE=virtual \
    PLANE_BUCKET_CORS=1 \
    MAIL_PROVIDER=resend \
    MAIL_BRIDGE_PORT=2525 \
    APP_PROTOCOL=https \
    FILE_SIZE_LIMIT=10485760 \
    ENABLE_SIGNUP=0 \
    ENABLE_EMAIL_PASSWORD=1 \
    ENABLE_MAGIC_LINK_LOGIN=0 \
    ENABLE_SMTP=1 \
    EMAIL_HOST=127.0.0.1 \
    EMAIL_PORT=2525 \
    EMAIL_USE_TLS=0 \
    EMAIL_USE_SSL=0 \
    EMAIL_FROM="Plane <plane@example.com>"

COPY docker/ /opt/plane-railway/

# - install our supervisor config over the upstream one
# - neutralise `clear` (upstream start.sh calls it; it only adds escape codes to logs)
# - byte-compile our helpers so a syntax error fails the build, not the deploy
RUN chmod 0755 /opt/plane-railway/entrypoint.sh /opt/plane-railway/*.py \
 && cp /opt/plane-railway/supervisor.conf /etc/supervisor/conf.d/supervisor.conf \
 && ln -sf /etc/supervisor/conf.d/supervisor.conf /etc/supervisord.conf \
 && printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/clear \
 && chmod 0755 /usr/local/bin/clear \
 && python3 -m py_compile /opt/plane-railway/bucket_cors.py /opt/plane-railway/mail_bridge.py /opt/plane-railway/crashwatch.py \
 && bash -n /opt/plane-railway/entrypoint.sh

EXPOSE 8080
CMD ["/opt/plane-railway/entrypoint.sh"]
