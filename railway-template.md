# Railway template checklist: Plane CE (lean)

Step-by-step for building the template in the Railway dashboard
(Workspace > Templates > **New Template**, the template composer).
Use a separate, throwaway Railway project for the test deploy and delete it
after testing (see TEST.md).

Final layout: **3 services + 1 bucket**

| Name (exact) | Kind | Source | Public | Volume |
|---|---|---|---|---|
| `Plane` | service | GitHub repo (this repo) -> builds `Dockerfile` | yes, port 8080 | none |
| `Postgres` | database | Railway PostgreSQL | no | yes (Railway default) |
| `Redis` | database | Railway Redis | no | yes, `/data` |
| `Bucket` | bucket | Railway Storage Bucket | (S3 endpoint is always public) | n/a |

Names matter: the variable references below use `Postgres`, `Redis` and
`Bucket`. If you rename a service, update every `${{Name.VAR}}`.

> **Published template:** https://railway.com/deploy/plane-ce (published 2026-11-22).
>
> **How the published version was built** (Railway's template generator keeps only variables containing `${{...}}`,
> drops plain values and service healthcheck timeouts, and needs a GitHub-app link to generate repo services):
> the template was generated from a project with placeholder images, then each repo service's source was switched
> to this GitHub repo in the template composer. Only secrets and references are template variables; every other
> setting has a default in the image (Dockerfile `ENV` / entrypoint `${VAR:-default}`). Railway injects `PORT=8080`;
> the public domain targets 8080. Postgres is `postgres:17.11` (volume `/var/lib/postgresql`); Redis is `redis:8.6.8` without a volume; Caddy listens on `:$PORT`.
> Verified by deploying the template itself into an empty project with zero inputs.

---

## 0. Before you start

- [ ] This repo is pushed to GitHub (public, so template users can deploy it).
- [ ] `Dockerfile` `FROM` line = `makeplane/plane-aio-community:v1.5.0@sha256:16a2680cc7ab0c1ef86396905563816246692e32302154cb2a4f335276ac03b0`.
- [ ] Re-check Railway docs for buckets / template variable functions (they change).

## 1. Postgres

- [ ] Add > **Database > PostgreSQL**. Rename service to `Postgres`.
- [ ] Settings > Source > Image: `ghcr.io/railwayapp-templates/postgres-ssl:17.11`
      (digest at time of writing: `sha256:5e806d28c18198f933d25e79989470a47a938f465bf0634e030b416961b3463e`;
      Railway may or may not accept `image@sha256:...` in the image field; if it
      does, use `ghcr.io/railwayapp-templates/postgres-ssl:17.11@sha256:5e806d28c18198f933d25e79989470a47a938f465bf0634e030b416961b3463e`).
      Plane upstream tests on Postgres 15; 17 is supported by its Django version.
- [ ] Keep Railway's default variables (`POSTGRES_PASSWORD=${{secret(32)}}`, `DATABASE_URL`, ...) and volume.
- [ ] No public networking (no TCP proxy).
- [ ] Optional: Resource limit memory 1 GB.

## 2. Redis

- [ ] Add > **Database > Redis**. Rename service to `Redis`.
- [ ] Settings > Source > Image: `redis:8.2.11`
      (digest `sha256:b0b01167ff47bc3868e56a967895a26be4230891a1f42a78ab61cdd227accc8d`).
- [ ] Variables (Railway's Redis database normally creates these; make sure they exist):
  - `REDIS_PASSWORD` = `${{secret(32)}}`
  - `REDISUSER` = `default`
  - `REDISHOST` = `${{RAILWAY_PRIVATE_DOMAIN}}`
  - `REDISPORT` = `6379`
  - `REDIS_URL` = `redis://${{REDISUSER}}:${{REDIS_PASSWORD}}@${{REDISHOST}}:${{REDISPORT}}`
    (no trailing `/` and no database number: the Plane entrypoint appends `/1` for the broker)
- [ ] Settings > Deploy > Custom Start Command:
  ```
  /bin/sh -c "exec docker-entrypoint.sh redis-server --requirepass $REDIS_PASSWORD --appendonly yes --dir /data --maxmemory 256mb --maxmemory-policy volatile-lru"
  ```
  `volatile-lru` only evicts keys that have a TTL (Plane's cache), never the
  Celery queues, which have none.
- [ ] Volume mounted at `/data`.
- [ ] No public networking.
- [ ] Optional: Resource limit memory 512 MB.

## 3. Bucket

- [ ] Add > **Bucket**. Name it `Bucket`. Pick the region closest to your users
      (cannot be changed later; it is fixed per deploy of the template).
- [ ] Nothing else: credentials are referenced from the Plane service, and the
      Plane container sets the bucket CORS rule itself on every boot.

## 4. Plane

- [ ] Add > **GitHub Repo** > this repository (branch `main`, root directory `/`).
      Rename service to `Plane`.
- [ ] Build: Railway picks up `railway.json` (Dockerfile builder, healthcheck
      `/api/instances/`, healthcheck timeout 600 s, restart ON_FAILURE x10,
      1 replica, draining 30 s). Confirm these show in Settings.
- [ ] Settings > Networking > **Generate Domain**, target port **8080** (Railway injects `PORT=8080`; Caddy listens on `:$PORT`).
- [ ] Do not attach a volume (Plane keeps no local state; logs go to stdout).
- [ ] Resource limits (recommended to avoid bill shock): memory **2 GB**, CPU 2 vCPU.
- [ ] Variables (Raw Editor, paste, then review):

```
DOMAIN_NAME=${{RAILWAY_PUBLIC_DOMAIN}}
APP_PROTOCOL=https
SECRET_KEY=${{secret(64, "abcdef0123456789")}}
LIVE_SERVER_SECRET_KEY=${{secret(64, "abcdef0123456789")}}
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
AWS_REGION=${{Bucket.REGION}}
AWS_ACCESS_KEY_ID=${{Bucket.ACCESS_KEY_ID}}
AWS_SECRET_ACCESS_KEY=${{Bucket.SECRET_ACCESS_KEY}}
AWS_S3_BUCKET_NAME=${{Bucket.BUCKET}}
AWS_S3_ENDPOINT_URL=${{Bucket.ENDPOINT}}
PLANE_S3_ADDRESSING_STYLE=virtual
FILE_SIZE_LIMIT=10485760
ENABLE_SIGNUP=0
ENABLE_EMAIL_PASSWORD=1
ENABLE_MAGIC_LINK_LOGIN=0
ENABLE_SMTP=1
EMAIL_HOST=127.0.0.1
EMAIL_PORT=2525
EMAIL_USE_TLS=0
EMAIL_USE_SSL=0
EMAIL_FROM=Plane <plane@example.com>
MAIL_PROVIDER=resend
MAIL_API_KEY=
MAIL_FROM=
PLANE_WORKER_CONCURRENCY=2
PLANE_GUNICORN_WORKERS=1
```

- [ ] Mark `MAIL_API_KEY` and `MAIL_FROM` as **optional** template inputs with
      descriptions:
  - `MAIL_API_KEY`: "Optional. Resend/Postmark/Brevo API key so Plane can send email on any Railway plan. Leave empty to skip email."
  - `MAIL_FROM`: "Optional. Verified sender, e.g. Plane <plane@yourdomain.com>."
  - `MAIL_PROVIDER`: "resend, postmark or brevo."
  If Railway rejects empty variables, delete `MAIL_API_KEY=` / `MAIL_FROM=`
  from the list and add them only as optional inputs.
- [ ] Do **not** set `AMQP_URL` (the entrypoint derives the broker from `REDIS_URL`).
- [ ] Do **not** expose Postgres/Redis publicly to make Plane work; it uses the private network.

## 5. Template metadata

- [ ] Name: `Plane | Open-Source Jira & Linear Alternative`
- [ ] Short description: `Lean Plane CE v1.5.0: 1 app container + Postgres + Redis + Bucket. No RabbitMQ/MinIO, email works on Hobby. [Updated Nov 2026]`
- [ ] Category: Project Management (or closest). Tags: `plane`, `project-management`,
      `jira-alternative`, `linear-alternative`, `kanban`, `issue-tracker`, `self-hosted`.
- [ ] Icon: a generic project-board icon (do not use Plane's logo).
- [ ] Overview: paste README sections "What you get", "Deploy", "Email",
      "Monthly cost estimate" and the "unofficial" note.
- [ ] Enable Template Queue email notifications (answering questions raises the kickback to 25%).

## 6. Test, then publish

- [ ] Deploy the template into a **new** project and run TEST.md end to end.
- [ ] Delete the whole test project after a few hours.
- [ ] Publish.

## Memory guidance (what to expect on the Metrics tab)

| Service | Idle | Busy | Limit we suggest |
|---|---|---|---|
| Plane | 0.8-0.9 GB (measured locally) | 1.1-1.8 GB (imports, exports, many users) | 2 GB |
| Postgres | 30-200 MB | 300-500 MB | 1 GB |
| Redis | 10-40 MB | < 256 MB (capped) | 512 MB |

If Plane approaches its limit: lower `PLANE_WORKER_CONCURRENCY` to 1 before
raising the limit. Raise `PLANE_GUNICORN_WORKERS` only for many concurrent users.
