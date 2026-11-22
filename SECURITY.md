# StockFlow security overview

For customers' IT and security teams. Describes what is built and tested in this repository; anything that depends on how a
customer deploys it is marked **Deployment**.

## Architecture

| Part | What it is | Exposed |
|---|---|---|
| Web app (`stock_mover/app.py`, Streamlit) | Planners and admins | HTTPS via the reverse proxy |
| API (`stock_mover/api.py`, FastAPI) | Phone app and other systems; OpenAPI docs at `/api/docs` | HTTPS via the reverse proxy |
| Phone app (`mobile/`) | Android, iOS and installable web app; talks only to the API | — |
| PostgreSQL | Plans, decisions, transfers, counts, users, audit | Private network only |
| Optional outbound | ERP/WMS (SFTP / HTTPS), webhooks, Teams/Slack alerts, WhatsApp Business API, Anthropic (AI answers, reading handwriting) | Outbound only, each off until configured |

## Identity and access

- Sign-in is required (an optional guest mode shows a fictional demo only and can be switched off with `guest_demo = false`).
- Passwords: PBKDF2-SHA256, 200,000 iterations, per-user salt. Policy: at least 10 characters, letters and digits, not containing the user name.
- Lockout after 5 failed attempts for 15 minutes; sign-out after 30 idle minutes; temporary passwords must be changed at first sign-in.
- Company sign-in (OIDC: Microsoft Entra ID, Google Workspace, Okta). MFA is enforced by the company's identity provider. **Not yet tested against a live tenant.**
- Roles: viewer, store, approver, admin (plus guest). Users can be limited to locations; a store user can only record dispatch, receipt and counts for its own locations, and sees only its own exceptions.
- API: signed tokens (HMAC-SHA256, 12 hours) or API keys for systems (stored as SHA-256 hashes, shown once, revocable).

## Data protection

- Customer columns in POS exports (names, mobile numbers, emails, loyalty ids) are never read: only mapped stock fields are kept.
- Stored input files (they contain sales lines) are purged after 90 days (`privacy.retention_days`).
- Secrets (database URL, API keys, SFTP passwords, tokens) live only in Streamlit secrets / environment variables; the Admin tab shows whether each is set, never the value. Tested: an ERP token never reaches any database table.
- AI features send data to Anthropic only after an admin confirms the data-processing agreement covers it; off by default.
- Encryption in transit: TLS at the reverse proxy (Caddy obtains and renews certificates; HSTS on). **Deployment:** encryption at rest is the database volume's or managed service's.

## Application security

- Every write is in an append-only audit trail (user, role, time, before/after); no code path edits or deletes audit rows (checked by test).
- Uploaded data shown on pages is HTML-escaped; every CSV/Excel export neutralises spreadsheet formulas (`=`, `+`, `-`, `@`).
- Zip uploads cannot write outside their folder; uploads are size-limited; image uploads are type-checked.
- Outbound calls (ERP, webhooks, alerts, downloads) accept only http(s) and refuse cloud-metadata addresses.
- Webhooks are signed (`X-StockFlow-Signature: sha256=…`) and logged.
- API responses: `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `no-store`; CORS limited to the app's own origins.
- The phone app caches only its own code, never stock data; actions taken offline are stored on the device until sent.

## Scans and tests (22 Nov 2026)

| Check | Result |
|---|---|
| Static analysis (Bandit, all Python code) | 0 high; the 5 medium findings (open URL schemes) fixed by the outbound URL check |
| Python dependencies (pip-audit) | No known vulnerabilities |
| Phone app dependencies (npm audit) | 0 vulnerabilities |
| Readiness checks (`tests/enterprise_check.py`) | See README; run on PostgreSQL and SQLite |
| API checks (`tests/api_check.py`) | Tokens, tampered tokens, roles, location scopes, API keys, webhook signatures, audit |

**Not done (needs a third party):** an independent penetration test, SOC 2 / ISO 27001 certification.

## Operations

- Backups: `scripts/backup.sh` (daily in `docker-compose.yml`, verified readable after each run, 30 days kept). Restore with
  `scripts/restore.sh`; a backup/restore round trip was tested table by table.
- Health: `/_stcore/health` (web), `/api/v1/health` (API, includes the database). Error tracking: set `SENTRY_DSN` (no personal data sent).
- **Deployment:** uptime monitoring, log retention and the backup copy off the server are the operator's.

## Reporting a vulnerability

Email security@<your-domain> with the details. Please do not open a public issue.
