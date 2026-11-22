# Plane | Open-Source Jira & Linear Alternative (lean Railway template)

Self-host **Plane Community Edition** (projects, work items, cycles, modules,
pages, intake) on Railway with the fewest services we could make reliable:
**one Plane container + Postgres + Redis + a Railway Bucket.** No RabbitMQ,
no MinIO, no separate web/admin/space/worker/beat containers.

> **Unofficial community template.** Not affiliated with or endorsed by Plane
> Software, Inc. Plane is AGPL-3.0 software by Plane Software, Inc. and
> contributors: <https://github.com/makeplane/plane>. This repository only
> holds deployment glue (MIT, see [LICENSE](LICENSE)).

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/plane-ce)

| | |
|---|---|
| Plane version | **v1.5.0** (released 2026-11-16, includes a large set of access-control security fixes) |
| Plane image | `makeplane/plane-aio-community:v1.5.0@sha256:16a2680cc7ab0c1ef86396905563816246692e32302154cb2a4f335276ac03b0` (official all-in-one image) |
| Postgres | `postgres:17.11` (digest-pinned), volume `/var/lib/postgresql` |
| Redis | `redis:8.6.8` (digest-pinned, no volume; cache **and** Celery broker) |
| File storage | Railway Bucket (S3-compatible, private, presigned URLs) |
| Idle RAM (measured locally) | Plane ~0.8-0.9 GB, Postgres ~30-40 MB, Redis ~10 MB |
| Expected cost | ~$10-16 / month of Railway usage for a small team (see below) |

## What you get

```
                    Railway edge (HTTPS)
                            |
              +-------------v--------------+        +-----------------+
              | Plane (1 container, :8080) |------->| Railway Bucket  |
              |  Caddy proxy               |  S3    | (attachments)   |
              |   /         web app        |        +-----------------+
              |   /god-mode admin          |              ^
              |   /spaces   public boards  |              | browser uploads /
              |   /api /auth  Django API   |              | downloads via
              |   /live     collaboration  |              | presigned URLs
              |  Celery worker + beat      |
              |  migrator (on every boot)  |
              |  SMTP->HTTPS mail bridge   |
              +------+--------------+------+
                     |              |
              +------v----+   +-----v------------------+
              | Postgres  |   | Redis                  |
              | (volume)  |   | db0 cache, db1 broker  |
              +-----------+   +------------------------+
```

The Plane service builds this repository's `Dockerfile`, a thin layer on the
official `makeplane/plane-aio-community` image. Plane itself is not rebuilt or
patched. The layer adds:

1. **Redis as the Celery broker.** Plane reads its broker from `AMQP_URL`;
   Celery accepts a `redis://` URL there. The entrypoint derives it from
   `REDIS_URL` (database 1, because Plane flushes database 0 on every API
   start). This removes the RabbitMQ service (~150 MB RAM + a volume).
2. **A capped worker pool.** Celery's default is one process per *host* CPU.
   On Railway that can be dozens of processes and several GB of RAM. We run
   `--concurrency=2` (`PLANE_WORKER_CONCURRENCY`) and recycle a worker child
   that grows past 384 MB.
3. **Railway Bucket support out of the box.** Virtual-hosted S3 URLs and an
   automatic, idempotent bucket CORS rule for your domain on every boot, so
   browser uploads work without a manual `aws s3api` step or an extra
   one-shot service.
4. **Email on the Hobby plan.** Plane only speaks SMTP, and Railway blocks
   SMTP on Free/Trial/Hobby. A tiny bridge inside the container accepts SMTP
   on `127.0.0.1:2525` and forwards to **Resend, Postmark or Brevo** over HTTPS.
5. **Railway-friendly operations.** All process logs go to the Railway log
   viewer (upstream writes them to files), stop signals reach every process,
   a crashed core process restarts the container instead of leaving it
   half-alive, and `supervisorctl status` works in `railway ssh`.
6. **Secure defaults.** Open sign-up is off, secrets are generated per deploy,
   only the Plane service is public.

## Deploy

1. Click **Deploy on Railway**. Nothing is required; optionally fill in the
   email variables (`MAIL_API_KEY`, `MAIL_FROM`).
2. Wait for the first deploy. The first boot runs all database migrations and
   takes **3-6 minutes**; the healthcheck waits up to 10 minutes.
3. **Immediately** open `https://<your-domain>/god-mode/` and create the
   instance admin account. Until you do, anyone who finds the URL can claim
   it. (This is how Plane works: the first visitor to `/god-mode/` becomes
   instance admin. This template does not pre-create the admin, so no admin
   password ever sits in your Railway variables.)
4. Sign in at `https://<your-domain>/`, create your workspace, invite people.

### Who can sign up

Open sign-up is **off** (`ENABLE_SIGNUP=0`). People can still create an
account when they have a pending workspace invitation for that email address:
invite them first, then send them the URL. Change this in
`/god-mode` > Authentication. The `ENABLE_*` and `EMAIL_*` variables are only
read on the very first boot; after that the values in `/god-mode` win.

## Email

| Railway plan | What to do |
|---|---|
| Free / Trial / Hobby | Set `MAIL_PROVIDER` (`resend`, `postmark` or `brevo`), `MAIL_API_KEY` and `MAIL_FROM` (a sender verified with that provider, e.g. `Plane <plane@yourdomain.com>`) on the Plane service and redeploy. Nothing to change in `/god-mode`. |
| Pro and above | Same as above, **or** point `/god-mode` > Email at your provider's SMTP server directly. |

Without `MAIL_API_KEY`, outgoing emails are logged (`[mail-bridge] ... dropped`)
and discarded. Plane still works: password login, invitations (the invitee
signs up with the invited address and sees the invite) and everything else.
Only "forgot password", magic-link codes and notification emails need email.

Test it: `railway ssh` into the Plane service, then
`cd /app/backend && python manage.py test_email you@example.com`.

## Monthly cost estimate

Railway bills usage: RAM $10/GB-month, CPU $20/vCPU-month, volumes
$0.15/GB-month, bucket storage $0.015/GB-month, egress $0.05/GB (Railway
pricing page, Nov 2026). Hobby includes $5 of usage in its $5 fee.

| Service | Expected idle RAM | Notes |
|---|---|---|
| Plane (all processes) | 0.8-0.9 GB measured (budget up to 1.3 GB busy) | API (1 worker), Celery worker (main + 2 children), beat, live, space, Caddy |
| Postgres | 30-40 MB measured idle, 100-300 MB busy | 1 GB volume is plenty for small teams |
| Redis | ~10 MB measured | capped at 256 MB |
| Bucket | - | pay per GB stored |
| **Total** | **~0.9-1.4 GB** | **≈ $10-16 / month** for a team of 1-15 at light-moderate use |

Idle numbers were measured with `docker stats` on the local compose stack
(same image and settings); Railway numbers can differ slightly, so check the
service's Metrics tab after a day. Things that raise the bill:
more Celery/gunicorn workers, big imports/exports, large attachments.

Ways to save: keep `PLANE_WORKER_CONCURRENCY=1` for 1-3 users; set a memory
limit on the Plane service (Settings > Resource limits, 2 GB is safe).

## Configuration reference (Plane service)

| Variable | Default in template | Purpose |
|---|---|---|
| `DOMAIN_NAME` | `${{RAILWAY_PUBLIC_DOMAIN}}` | Host users open. Set to your custom domain after adding it. |
| `APP_PROTOCOL` | `https` | Railway terminates TLS at its edge. |
| `PORT` | Railway's `8080` (not set by the template) | Caddy listens on `:$PORT` (`SITE_ADDRESS`, falls back to 80 when `PORT` is unset, e.g. locally). The public domain and healthcheck use 8080. |
| `SECRET_KEY` | `${{secret(64, "abcdef0123456789")}}` | Django key; also encrypts saved instance settings. **Never change it.** |
| `LIVE_SERVER_SECRET_KEY` | `${{secret(64, "abcdef0123456789")}}` | Live collaboration server secret. |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Private network URL. |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Cache (db 0) and, by default, broker (db 1). |
| `AMQP_URL` | *(unset)* | Set an `amqp://` URL only if you add your own RabbitMQ. |
| `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL` | `${{Bucket.REGION}}`, `${{Bucket.ACCESS_KEY_ID}}`, `${{Bucket.SECRET_ACCESS_KEY}}`, `${{Bucket.BUCKET}}`, `${{Bucket.ENDPOINT}}` | Railway Bucket credentials. |
| `PLANE_S3_ADDRESSING_STYLE` | `virtual` | `path` for MinIO or older Railway buckets. |
| `PLANE_BUCKET_CORS` | `1` | `0` to manage the bucket CORS rule yourself. |
| `PLANE_EXTRA_CORS_ORIGINS` | *(empty)* | Extra comma-separated origins for the bucket CORS rule. |
| `FILE_SIZE_LIMIT` | `10485760` | Max upload in bytes (10 MB). |
| `ENABLE_SIGNUP` | `0` | First boot only, then `/god-mode`. |
| `ENABLE_EMAIL_PASSWORD` / `ENABLE_MAGIC_LINK_LOGIN` | `1` / `0` | First boot only. |
| `ENABLE_SMTP`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_FROM` | `1`, `127.0.0.1`, `2525`, `0`, `0`, `Plane <plane@example.com>` | Points Plane at the built-in mail bridge. First boot only. |
| `MAIL_PROVIDER` | `resend` | `resend`, `postmark` or `brevo`. |
| `MAIL_API_KEY` | *(empty)* | Provider API key / server token. Empty = emails are dropped. |
| `MAIL_FROM` | *(empty)* | Overrides the sender of every email. Must be verified with the provider. |
| `PLANE_WORKER_CONCURRENCY` | `2` | Celery worker processes (1-16). |
| `PLANE_GUNICORN_WORKERS` | `1` | API worker processes (1-8). |
| `PLANE_WORKER_MAX_MEMORY_KB` | `393216` | Recycle a Celery child above this RSS. |
| `PLANE_BROKER_REDIS_DB` | `1` | Redis database used as the broker. |

## Custom domain

1. Plane service > Settings > Networking > **Custom Domain** (target port 8080), add the DNS record.
2. Set `DOMAIN_NAME` to the custom domain (no `https://`) and redeploy.
   Plane's `WEB_URL`, CORS and cookies follow `DOMAIN_NAME`, and the bucket
   CORS rule is updated automatically on boot.
3. Sign in again on the new domain (cookies are per host).

## Upgrading Plane

Upgrades are a one-line change, tested by us before we bump the template.

1. Read the [Plane release notes](https://github.com/makeplane/plane/releases)
   for every version between yours and the target.
2. **Back up first** (next section).
3. In the `Dockerfile`, replace the tag **and** digest on the `FROM` line.
   Get the digest with
   `docker buildx imagetools inspect makeplane/plane-aio-community:vX.Y.Z`
   (the top-level `Digest:` line).
4. Diff upstream's `deployments/aio/community/supervisor.conf` and
   `start.sh` between the two versions; port any change into
   `docker/supervisor.conf`.
5. Redeploy. The migrator runs automatically on boot; the API waits for it.
   Watch the logs for `No migrations Pending` / the API starting.

If you deployed from the template, Railway shows "update available" on the
Plane service when this repository moves to a new version; review and apply it.
Downgrades are not supported (database migrations are one-way): restore the
backup instead.

## Backups and restore

**Postgres** holds all project data. Enable Railway volume backups on the
Postgres service (Backups tab) and/or take dumps:

```bash
# from your machine, with the Postgres service's public URL (enable TCP proxy temporarily)
pg_dump "$DATABASE_PUBLIC_URL" --format=custom --file plane-$(date +%F).dump
# restore into an empty database
pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_PUBLIC_URL" plane-YYYY-MM-DD.dump
```

**Bucket** holds attachments. Railway buckets have no automatic backups; copy
them with any S3 tool using the bucket's credentials:

```bash
aws s3 sync s3://<BUCKET> ./plane-bucket-backup --endpoint-url https://t3.storageapi.dev
```

**Redis** only holds cache and queued jobs; it does not need backups.
Keep a copy of `SECRET_KEY`: without it, instance settings saved in
`/god-mode` (e.g. OAuth secrets) cannot be decrypted after a restore.

## Troubleshooting

- **Deploy keeps "waiting for healthcheck":** first boot migrations take
  minutes. Check the logs for `migrator` output. If the API says it is waiting
  for migrations forever, the migrator failed: its error is in the logs.
- **Uploads fail (CORS error in the browser console):** look for
  `[bucket-cors]` in the boot logs. If it says it could not set CORS, set it
  once by hand (Railway docs: Storage Buckets > CORS) with your origin and
  methods GET, HEAD, PUT, POST, then set `PLANE_BUCKET_CORS=0`. If uploads
  fail with a DNS/SSL error, set `PLANE_S3_ADDRESSING_STYLE=path`.
- **Emails not arriving:** check `[mail-bridge]` log lines. `HTTP 403/422`
  usually means `MAIL_FROM` is not a verified sender/domain.
- **Process status:** `railway ssh` into the Plane service, then
  `supervisorctl status`. For Celery commands in a shell, load the derived
  settings first: `set -a; . /app/plane.env; set +a; cd /app/backend`
  (e.g. `celery -A plane inspect active`).
- **Container restarts in a loop:** a core process is failing repeatedly;
  the log line `[crashwatch] '<name>' entered FATAL state` names it.

## Limits and notes

- Run **one replica** of the Plane service (Celery beat must be unique).
- During a redeploy Railway briefly runs the old and new container together;
  a periodic job may run twice in that window (harmless for Plane's jobs).
- Plane's `/spaces` public boards and `/live` collaboration are included and
  served from the same domain.
- Enterprise/Business features of Plane are not part of Community Edition.

## Credits and license

- Plane by Plane Software, Inc. and contributors, AGPL-3.0:
  <https://github.com/makeplane/plane>. Source for the exact version used is
  the `v1.5.0` tag of that repository.
- `docker/supervisor.conf` is adapted from Plane's AIO supervisor config and
  stays AGPL-3.0.
- Everything else in this repository: MIT, see [LICENSE](LICENSE).
