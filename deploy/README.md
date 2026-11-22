# Deploying StockFlow on your own server or cloud

One Linux server (4 GB RAM for up to ~300 locations, 8 GB for 500) with Docker runs everything: the web app, the API
and phone app, PostgreSQL, HTTPS certificates and daily backups. Pick a region in India (AWS Mumbai / Hyderabad,
Azure Central India, GCP Mumbai / Delhi) to keep data in India.

## First start

```bash
git clone https://github.com/OmedullaAhmed/SYNAPSE.git stockflow && cd stockflow
cp deploy/.env.example .env                       # set DOMAIN, DB_PASSWORD, STOCKFLOW_API_SECRET (long random strings)
cp deploy/secrets.example.toml deploy/secrets.toml # optional: company sign-in, ERP, WhatsApp, alerts
docker compose up -d --build
```

Point DNS for `DOMAIN` and `api.DOMAIN` at the server first; certificates are then issued automatically.

- Web app: `https://DOMAIN`. The first visitor creates the admin account, so open it straight away.
- Phone app: `https://api.DOMAIN/app/` (install from the browser), or build the Android / iOS apps (`mobile/README.md`).
- API docs: `https://api.DOMAIN/api/docs`.

## Checklist before real data

- [ ] `guest_demo = false` in `deploy/secrets.toml`
- [ ] Admin account created; users added (Admin tab, or a CSV of many users)
- [ ] Company sign-in configured and tested with one user, if used
- [ ] Backups: check `./backups` has a file after the first day; copy that folder off the server (object storage)
- [ ] Restore tested once on a spare database (`scripts/restore.sh`)
- [ ] Uptime monitor on `https://DOMAIN/_stcore/health` and `https://api.DOMAIN/api/v1/health`
- [ ] `SENTRY_DSN` set if you use error tracking
- [ ] Data protection: `deploy/DATA_PROTECTION_DRAFT.md` reviewed and agreed with the customer

## Managed alternatives

- Database: any PostgreSQL 14+ (AWS RDS, Azure Database for PostgreSQL, Neon, Supabase). Set `DATABASE_URL` and drop the
  `db` and `backup` services; use the provider's backups.
- Containers: the same image runs on AWS App Runner / ECS, Azure Container Apps or Google Cloud Run: one service with the
  default command (web app, port 8501) and one with `uvicorn api:app --host 0.0.0.0 --port 8000` (API).

## Updating

```bash
git pull && docker compose up -d --build
```

New database tables are created automatically on start and nothing is dropped. A release that changes existing columns says so in `stock_mover/CHANGES.md`.

## Not verified in the build environment

The image could not be built where this was written (no access to Docker Hub). Build it once on your server and run
`docker compose ps` (all services `healthy`) before going live.
