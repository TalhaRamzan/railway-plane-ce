# TEST.md: Plane CE (lean)

Two parts: what was already verified without Railway, and the checklist to
run on a real Railway test deploy before publishing (and after every version
bump). Keep the Railway test short (a few hours) and delete the project after.

## A. Static / local verification (done while building this repo)

| Check | How | Result |
|---|---|---|
| Plane latest stable release | GitHub releases API | `v1.5.0` (2026-11-16), previous `v1.4.3` |
| AIO image tag and digest exist | Docker Hub API + registry `HEAD` manifest | `makeplane/plane-aio-community:v1.5.0` = `sha256:16a2680c...03b0` (amd64 + arm64) |
| AIO image matches upstream repo files | registry image config (`Cmd`, ports, layer history) vs `deployments/aio/community/*` at tag `v1.5.0` | `CMD /app/start.sh`, ports 80/443, same layers |
| Redis image digest | registry `HEAD` | `redis:8.2.11` = `sha256:b0b01167...cc8d` |
| Postgres image digest | ghcr `HEAD` | `postgres-ssl:17.11` = `sha256:5e806d28...463e` (local compose: `postgres:17.11-alpine` = `sha256:e5d2edd2...00d0`) |
| Local S3 stand-in digest | Docker Hub API | `pgsty/silo:RELEASE.2026-09-16T00-00-00Z` = `sha256:635197cb...1a46` |
| Plane can use Redis as broker | read `apps/api/plane/settings/common.py` (`CELERY_BROKER_URL = AMQP_URL`) and `celery.py`; no AMQP-only features (`kombu`, priorities, queues) in Plane code; kombu 5.5.4 resolves the derived URL to `kombu.transport.redis` | OK |
| Longest Celery countdown < Redis visibility timeout (1 h) | grep `countdown` / `retry_backoff` | 300 s (manual sync commands), webhooks 600 s max |
| Redis db 0 is flushed by Plane | `clear_cache` command calls `cache.clear()` on every API start | yes, so broker uses db 1 |
| botocore honours `AWS_CONFIG_FILE` addressing style with Plane's exact client construction | boto3 presigned POST/GET with `endpoint_url=https://t3.storageapi.dev` | `virtual` -> `https://<bucket>.t3.storageapi.dev/`, `path` -> `https://t3.storageapi.dev/<bucket>` |
| `bucket_cors.py` | botocore `Stubber`: no rule -> put; same rule -> skip; AccessDenied -> warning, exit 0 | OK |
| `mail_bridge.py` | real `smtplib` client against the bridge, HTTP mocked: Resend, Postmark, Brevo payloads; no-key drop; provider 403 -> SMTP 451; dot-stuffing; UTF-8 subject; attachment; STARTTLS refused | OK |
| `crashwatch.py` | real supervisord 4.x with a failing program | FATAL -> marker file -> supervisord stops -> entrypoint exits 1 |
| `entrypoint.sh` | `bash -n`; simulated run with a fake `start.sh` + real supervisord: domain from `RAILWAY_PUBLIC_DOMAIN`, broker URL `.../1` (password with `%40` kept), out-of-range concurrency -> 2, `GUNICORN_WORKERS` written to plane.env, SIGTERM -> clean stop, exit 0 | OK |
| `supervisor.conf` | parsed with supervisor's own `ServerOptions` | 8 programs + 1 event listener |
| `docker-compose.yml` | PyYAML parse + `docker compose config -q` | OK |
| `railway.json` | keys checked against `https://railway.com/railway.schema.json` | OK |
| Local `docker compose` run | see section B | PASSED, details in section D |

## B. Local docker compose run

```bash
cp .env.example .env        # fill the 5 secrets with: openssl rand -hex 32
docker compose up --build -d
docker compose logs -f plane   # wait for the API to start (first boot: several minutes)
```

- [ ] `docker compose ps` shows `plane` healthy.
- [ ] Logs show `[plane-railway] Celery broker: Redis database 1 (no RabbitMQ)`.
- [ ] Logs show the migrator finishing and `celery@... ready.` from the worker.
- [ ] `docker compose exec plane supervisorctl status`: all programs RUNNING
      (migrator EXITED is expected).
- [ ] `docker compose exec plane ps -o pid,rss,args` shows exactly 2 Celery pool children.
- [ ] `http://localhost/god-mode/` -> create admin -> `http://localhost/` -> sign in.
- [ ] Create workspace, project, work item, page (live editing), upload an attachment
      (uses `s3.localhost:9000`; if your browser does not resolve `*.localhost`,
      add `127.0.0.1 s3.localhost` to `/etc/hosts`).
- [ ] `docker compose restart plane` -> data and attachment still there.
- [ ] `docker compose down` then `up -d` -> data still there.
- [ ] `docker stats --no-stream` -> note idle RAM of `plane`.
- [ ] `docker compose down -v` to clean up.

Results of the run made while building this repo are recorded in section D.

## C. Railway test deploy checklist (you, before publishing)

Create a **new** Railway project, deploy the template from the composer,
then:

**Boot**
- [ ] Plane build succeeds (the `VOLUME` line inherited from the base image does not block the build).
- [ ] First deploy turns healthy within 10 minutes. Note the time: ____
- [ ] Logs show `[bucket-cors] set for https://<domain>` (not a WARNING).
- [ ] Logs show `Celery broker: Redis database 1` and the worker `ready`.
- [ ] Logs show no `[crashwatch]` lines.

**Security defaults**
- [ ] `https://<domain>/god-mode/` -> create admin immediately.
- [ ] Sign out; try to sign up with a new email at `https://<domain>/` -> rejected (sign-up disabled).
- [ ] Postgres and Redis have no public domain / TCP proxy.
- [ ] `SECRET_KEY` and `LIVE_SERVER_SECRET_KEY` are different 64-char hex values.

**Core features**
- [ ] Create workspace, project, work items, cycle, module.
- [ ] Upload an image attachment and a PDF to a work item; both preview/download.
      (Proves presigned POST + CORS + virtual-hosted URLs on the Railway bucket.
      If it fails: try `PLANE_S3_ADDRESSING_STYLE=path`, note which works.)
- [ ] Workspace logo / profile avatar upload works.
- [ ] Create a page, open it in two browsers, edit together (live server over `/live/`).
- [ ] Publish a project to Spaces and open the public link (`/spaces/`).
- [ ] Invite a second email; sign up with that email in a private window -> allowed (invite exists).

**Email (with a real Resend test key + verified sender)**
- [ ] Set `MAIL_API_KEY`, `MAIL_FROM`, redeploy.
- [ ] `railway ssh` -> `cd /app/backend && python manage.py test_email you@example.com` -> arrives.
- [ ] Workspace invitation email arrives; "forgot password" email arrives.
- [ ] Without `MAIL_API_KEY`: log shows `[mail-bridge] MAIL_API_KEY not set; dropped email ...`.

**Background jobs**
- [ ] `railway ssh` -> `supervisorctl status` -> api, worker, beat, live, space, proxy, mailbridge RUNNING.
- [ ] Logs show beat sending `check-every-five-minutes-...` tasks and the worker running them.
- [ ] `ps -o pid,rss,args | grep celery` -> 1 main + 2 pool processes (not one per host CPU).

**Persistence and restarts**
- [ ] Redeploy Plane -> data, attachments, login session survive.
- [ ] Restart Postgres -> Plane recovers without manual action.
- [ ] Restart Redis -> Plane recovers; queued jobs still run.
- [ ] `railway ssh` -> `supervisorctl stop api` is NOT how to test crashwatch (manual stop is not FATAL);
      optional: `kill -9` the api 11 times quickly or trust the local test.

**Custom domain (optional)**
- [ ] Add a custom domain, set `DOMAIN_NAME`, redeploy -> sign in and upload work; boot log shows CORS set for both domains.

**Cost**
- [ ] After 2-3 hours idle: Metrics tab RAM per service. Plane: ____ MB, Postgres: ____ MB, Redis: ____ MB.
- [ ] Update README "Monthly cost estimate" with the measured numbers.

**Clean up**
- [ ] Delete the whole test project (services, volumes, bucket).

## D. Recorded results

### Local run results (2026-11-21, OrbStack, Docker 29.8.2, linux/amd64)

Stack: `docker compose up` of this repo (Plane image built from the
Dockerfile, `postgres:17.11-alpine`, `redis:8.2.11`, `pgsty/silo` as the S3
stand-in). Functional steps were driven through Plane's HTTP API with a
cookie session (the same endpoints the web UI calls), not by clicking.

| Check | Result |
|---|---|
| Image build (FROM pinned digest, py_compile + `bash -n` inside build) | PASS |
| Fresh first boot (empty DB, all migrations) to healthy | **243 s** (healthcheck `/api/instances/` 200) |
| Restart / recreate to healthy (no migrations pending) | 33-34 s |
| `/`, `/god-mode/`, `/spaces/`, `/api/instances/` | all HTTP 200 |
| All supervisor programs RUNNING, migrator EXITED (0) | PASS (`supervisorctl status`, no `-c` needed) |
| Celery broker = Redis db 1, no RabbitMQ | PASS: worker log `Connected to redis://default:**@redis:6379/1`; beat tasks (`stack_email_notification`, `unsnooze_expired_intake_issues`) received and completed (`celery inspect stats` total > 0, `inspect active` empty) |
| Celery pool capped | PASS: 1 main + exactly 2 pool children (host has 16 CPUs; upstream default would be 16) |
| Instance admin via `/god-mode` sign-up | PASS (`is_setup_done=true`, version v1.5.0) |
| Open sign-up disabled | PASS: new email -> `error_code=5015 SIGNUP_DISABLED` |
| Sign in, create workspace, project, work item | PASS |
| Attachment upload: presigned POST from client straight to S3 | PASS (HTTP 204), object present in the bucket |
| Attachment download: API 302 -> presigned GET, content identical | PASS |
| Bucket CORS step | PASS: first boot creates the missing bucket and sets the rule; later boots log `already set` |
| Email path: `manage.py test_email` -> Plane SMTP -> mail bridge | PASS (no key: logged `dropped`; provider HTTP calls covered by the mocked tests in section A) |
| Graceful stop (`docker compose stop`) | PASS: SIGTERM reached all programs, live server flushed documents, exit code 0 in 11 s |
| Data after `restart` and after `down` + `up` (volumes kept) | PASS: login, work item and attachment all still there |
| Teardown `docker compose down -v` | done, no containers or volumes left |

Measured idle memory (`docker stats --no-stream`, a few minutes after boot):

| Container | RAM |
|---|---|
| plane (all processes) | 805-878 MiB |
| postgres | 27-41 MiB |
| redis | 9 MiB |
| s3 stand-in (local only, not on Railway) | 83-99 MiB |
| **Railway-equivalent total (plane + postgres + redis)** | **~0.85-0.93 GiB** |

Per process inside plane (RSS): live 207 MB, celery worker main 167 MB +
2 children ~145 MB each, beat 166 MB, gunicorn worker 162 MB, space 151 MB,
caddy 47 MB, supervisord/mail bridge/gunicorn master ~25 MB each.

Fixes made because of this run: bucket created before the CORS rule on an
empty S3 (local only; Railway buckets always exist); `/etc/supervisord.conf`
symlink so plain `supervisorctl` works; README note on loading
`/app/plane.env` before running Celery commands in a shell.

Not covered locally (needs the Railway test in section C): Railway Bucket
(Tigris) presigned POST + CORS + virtual-hosted URLs, real email delivery
through Resend/Postmark/Brevo, Railway healthcheck/restart/draining
behaviour, Railway reference variables, two-browser live editing in the UI,
memory on Railway's metrics.

### Railway test deploy results

_Not run yet._ Fill in section C's blanks here.

### Railway deploy results (2026-11-21, Hobby plan, project created 19:37 UTC, deleted 19:43 UTC)
- PASS: Plane builds from this GitHub repo; `railway.json` healthcheck (`/api/instances/`) passes; deployment SUCCESS ~2 min after connect, migrations finish shortly after.
- PASS: references to Railway Postgres, Railway Redis (`REDIS_URL` without db number) and the bucket (`${{Bucket.*}}`) resolve.
- PASS: broker: Celery connected to `redis://…@redis.railway.internal:6379/1`; no RabbitMQ.
- PASS: Railway bucket (Tigris, endpoint `t3.storageapi.dev`) accepted the CORS rule set on boot: preflight from the Plane domain → 200 with `Access-Control-Allow-Origin: <plane domain>`, other origins → 403.
- PASS: `/` 200, `/god-mode/` 200, `/api/instances/` 200 with `enable_signup: false`, `is_smtp_configured: true` (mail bridge), file size limit 10 MB.
- Measured RAM on Railway (idle, fresh): Plane ~790 MB, Postgres ~130 MB, Redis ~27 MB → ~0.95 GB ≈ $9–10/month RAM.
- Note: Railway's default Redis database service was used (not the custom start command with `maxmemory`); works fine, the custom command is optional hardening.
- Not tested on Railway (passed locally): admin setup, workspace/project/issue, presigned upload from a browser.
