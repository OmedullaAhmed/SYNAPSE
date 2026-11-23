# StockFlow: stock planning for any business with stores and warehouses

Upload yesterday's sales and stock (or photograph a count sheet) and get back a short list of **transfers** between
warehouses and stores to approve, each with a one-line reason, what it gains and what it costs to move. Built for chains
of 2 to 500 locations selling anything: apparel and footwear (sizes and colours kept as sets), electronics, home goods,
grocery and FMCG, pharmacy (expiry-aware), spare parts. Approvers work on the web or on the Android / iOS app.

| Piece | Where |
|---|---|
| Web app for planners and admins | `app.py` (Streamlit) |
| REST API for the phone app and other systems | `api.py` (FastAPI, docs at `/api/docs`) |
| Phone app (Android, iOS, installable web app) | `../mobile` |
| Self-hosting (Docker, HTTPS, PostgreSQL, backups) | `../deploy`, `../docker-compose.yml` |
| Security overview | `../SECURITY.md` |

```
pip install -r requirements.txt
python make_demo_data.py            # apparel demo ("Demo Threads") in data/demo
python tests/make_general_demo.py data/demo_general   # general-retail demo ("Demo Mart"): no sizes, expiry dates
streamlit run app.py                # press a demo button, or upload your own exports
uvicorn api:app --port 8000         # API + phone app at http://localhost:8000/app/
python engine.py --data <folder or workbook> [--config brands/x.json] --out output   # command line / scheduler
```

## What it does each day

| Step | What happens |
|---|---|
| Clean | Sheets are checked and common column names mapped. Customer and staff columns are dropped. Stock records that disagree, negative stock, and damaged, alteration or QC stock are flagged and never moved. |
| Demand | Selling speed counts only the days a size was on the shelf. New stores and new launches borrow from similar stores. Peak days and event weeks come from your calendar. |
| Plan | **Warehouse → store** from any number of warehouses (home warehouse first). **Size-set completion**: an option on display that is missing a core size gets it. **Minimum display** top-ups. **Store → store** transfers, preferring stores that already have a trip to the same destination. **Old stock by option**: broken size runs move together to one store or come back together. **Capped returns** per store (pieces and value), with the rest queued. **End of season**: no refill lands after it; leftovers go to an outlet or the warehouse. |
| Money | Every line is valued: expected extra sales × margin (net of real discount, tax and cost), plus markdown and holding cost avoided, minus freight, handling and a share of the trip cost. Lines and trips that don't pay are dropped. Rule-driven display top-ups always stay. |
| Approve | About 25 decisions cover the day. One-line clubbed reasons: *Refill before it runs out · Stock up for the peak ahead · Complete broken size sets · Fill minimum display · Top up to normal cover · Old stock to faster-selling stores · Reunite broken size runs · Return slow stock · Clear end-of-season / dead stock*. |
| Hand-off | Generic transfer-order CSV, one TO per trip, for any ERP or WMS. |
| WhatsApp | On release, area managers, stores and warehouses with a `whatsapp` number get their part of the plan. When an order leaves, the receiving end is told; when it arrives short or over, both ends are. Sent automatically with the WhatsApp Business API (Meta or Twilio, set in secrets), otherwise one tap per person. |
| Tracking | Every transfer order goes Released → Left → Received, with times. Mark it in the app (or the quantity that really moved), or upload dispatch and receipt files. Late leaving, late arriving and short/over receipts are flagged. |
| Check | 17 rules are re-checked on the final plan, and the run fails if any breaks. |

## Data

See **DATA_GUIDE.md**. You can also open the **Data guide** tab in the app, which has downloadable blank templates.
The minimum is four sheets: stores, items, sales, stock_snapshot.

## Database, sign-in and audit

**Database.** Plans, every recommended line, line decisions, released transfer orders, dispatch and receipt events,
settings, users and the audit trail are stored in PostgreSQL. Put the URL in the app's secrets:

```toml
[database]
url = "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
```

A free Neon (neon.tech) or Supabase database is enough for a small chain. The tables are created on first start, and
any CSV history from older versions is imported once. Without a URL the app falls back to a local SQLite file and
says so on every page and in the Admin tab; Streamlit Community Cloud wipes that file on restart.

**Recovery after a restart.** Each plan stores its input files (up to 60 MB) and settings. "Pick up where you left off"
on the start page reopens any saved plan: the engine is deterministic, so it rebuilds the same lines, and the saved
line decisions, releases and tracking come back with it.

**Guest demo.** The sign-in page has "Explore the demo without signing in". It opens the fictional demo brand only:
guests can browse every dashboard and try line approvals, but can't upload data, release orders, track, change settings
or see the Audit and Admin tabs. Guest visits are audited as user `guest`. Turn it off for a live brand with
`guest_demo = false` in secrets (or `STOCKFLOW_GUEST_DEMO=false`).

**Sign-in is mandatory** for real data. With no users anywhere, the first visitor creates the admin account. Admins add users in the
Admin tab with a temporary password, which must be changed at first sign-in (10+ characters, letters and numbers, not
the user name). Users can also be listed in secrets with hashed passwords (`python auth.py`); those win and can't be
edited in the app. Five wrong passwords lock an account for 15 minutes; 30 idle minutes sign a user out.

| Role | Can |
|---|---|
| viewer | see plans and dashboards |
| store | + mark dispatch and receipt for its own stores |
| approver | + approve, reject or reduce lines, release orders, track all stores, read the audit trail |
| admin | + settings, users |

A user can be limited to some stores. Secrets (database URL, API keys, WhatsApp token) live only in Streamlit secrets
or environment variables; the Admin tab shows whether each is set, never its value.

**Audit trail.** Sign-ins (and failures), plan builds and reopens, each line decision with before and after quantity,
bulk decisions, releases, downloads, dispatches, receipts, uploads, settings changes, WhatsApp sends and feedback are
recorded with user, role and timestamp. The Audit tab filters and exports them; the app has no way to edit or delete them.

**Line-level approval.** On the Approve tab, "Line by line" lists every recommended line. Untick to reject, lower the
quantity (it can't go above the recommendation), add a note, and save. Transfer orders and the release then use the
edited lines. Trip-level toggles still work as before.

**Inventory dashboard.** The Inventory tab shows chain-wide pieces and value (stores, warehouses, in transit), cover,
size availability and old stock, by category, and a store-by-store comparison (shelf used, cover, sizes on shelf,
old stock, hidden stockouts, pieces in and out in today's plan) with zone, tier and category filters and a CSV export.

## Any business (Nov 2026)

**Business types.** Pick one when uploading: apparel, general retail, electronics, grocery / FMCG, pharmacy, spare parts.
Each sets sensible starting values (cover days, when stock counts as old, expiry window, handling cost); all can be tuned.
Sizes, styles and colours are optional: a product master with just SKU, name and price works.

**Expiry dates.** Add `expiry_date` to the stock file (earliest expiry of those units). Expired stock is never moved and is
reported; stock expiring within the window (30 days by default, 21 for grocery, 90 for pharmacy) is treated as old and
sent to stores that will sell it in time.

**Stock from photos and scans.** Upload or photograph (web or phone): barcodes are counted on the server (each scan =
one unit), printed count sheets and registers are read by OCR on the server, and handwritten registers can be read by AI
once an admin switches it on (the image then goes to Anthropic). Lines that are unclear or not in the product list are
shown first for checking. A saved count replaces the system figure in the next plan for that day ("everything at the
location" counts set uncounted items to zero) and raises a *Count differs from system* exception with the value at stake.
With no stock file at all, counts alone become the stock. Tested: 18/18 lines from a printed sheet, 17-18/18 from a tilted,
blurred phone photo, every barcode on a shelf photo.

**Phone app.** Approve trips by region, release, mark transfers sent and received (scan to check), count stock by scanning
or photographing a sheet, resolve issues. Works offline: actions are kept and sent when the signal returns.

**API and webhooks.** Everything the phone app does is in the REST API (`/api/docs`), with user tokens or API keys for
systems, location scopes and the same audit trail. Signed webhooks for plan.ready, trips.released, transfer.dispatched,
transfer.received, exception.opened, count.saved.

**Approval deadlines.** Trips not decided by the cut-off (11:00 on the plan day by default; `operations.approve_by`) raise
an *Approvals overdue* exception per region, high if urgent refills are waiting, and alert Teams/Slack via webhooks.

**What to buy.** The Money tab lists stock no transfer can supply: store shortfalls after the plan, less spare warehouse
stock and supplier deliveries due, rounded to pack size / minimum order (optional item columns `vendor`, `pack_size`, `moq`).

**Forecast accuracy.** How it works → "Measure it": the plan is rebuilt as of 4 weeks ago and its forecast compared with
what then sold (only where the item was on the shelf), next to the simple 28-day average. Store × category error on the
demo data: apparel 25% vs 32%, general retail 11% vs 12%, toys 20% vs 22%, hackathon 48% vs 51%. Week by week (KPIs tab)
StockFlow and the simple average are level (within ±1 point; hackathon 39% vs 43%) and StockFlow under-predicts less.

**Forecast changes (Nov 2026).** Items already selling are forecast even if their launch date is still ahead (reported as
a data issue). The selling speed blends the last 4 weeks with the longer fading average (`planning.recent_blend`, 0.5) and
carries each category's recent trend forward (`planning.trend_strength`, 0.5, capped 0.7–1.6×). Tested on four datasets
with an 8-week replay: under-prediction cut from −13/−17/−20/−5% to −8/−16/−12/−4%. Still weak: festival peaks the
history has never seen (needs a year of sales); the remaining gap to the noise floor (about 20% for apparel) is there.

## KPIs tab

Today's stock KPIs (stock on hand, days of cover, in-stock rate, items at risk of running out, old stock, 30-day
sell-through, stock turns, demand next 7 days), each shown now and after today's plan, by store too. **Predicted vs
actual:** for each of the last 8 weeks the forecast is rebuilt from the data as it stood that week and compared with
what sold (only where the item was on the shelf), next to a simple 4-week average, by store and category. Every saved
plan also records its KPIs and 7-day forecast, so trend charts and forecast-vs-actual by plan day build up over time.

## Operations at scale

**Data integration.** Upload (or drop in the daily folder) exports exactly as the POS, ERP or WMS produces them. Each file
is recognised (sales, stock, product master, store list …), its columns are mapped to the standard sheets (saved mapping
profile → exact name → a vocabulary of common POS/ERP headers → close spelling), and values are cleaned: day-first dates,
Excel serial dates, ₹ and comma-formatted amounts, `(12)` negatives, title rows above the header, `;` or tab separators,
Windows-1252 text, lower-case codes, and return lines flagged by a type column. Columns that are not needed (customer
mobile numbers, names) are never read. Correct a mapping once in **Integrations → Column mapping profiles**; every later file
with those headers is read the same way. Tested: the demo brand rewritten as vendor-style exports gives the identical plan.

**Data-quality alerts.** Each load is checked for stale or mixed-date stock, negative and duplicated stock rows, SKUs missing
from the product master, stores that sent no stock, missing warehouse stock, sales gaps and sales that stop before the stock
date. These, plus stock-data problems the planner finds, become **exceptions**.

**Exceptions.** One queue (Exceptions tab) for data problems and transfer discrepancies (late leaving, late arriving,
received short or over). The same problem seen again is updated rather than duplicated; problems that disappear are closed
as "cleared"; people assign and resolve the rest with a note. Every action is audited.

**Regional approval queues.** Operations tab: each trip sits in the queue of the region it serves. Approvers limited to some
stores see their regions only. Approve or reject trips (or all pending in a queue), then release the approved ones. A trip
rejected in a queue is left out of the transfer orders everywhere. Transfer status is shown by region.

**ERP / WMS exchange.** Released transfer orders are sent automatically, as CSV or JSON in the ERP's own field names, to a
folder, an SFTP server (written as `.part`, then renamed) or an HTTP API. Dispatch and receipt confirmations come back the
same way (file name or an `event` column says which), are applied once each (checksums), archived, and update tracking and
exceptions. See `[erp]` in `secrets.example.toml`.

**Daily automation.** `python daily.py` picks up ERP confirmations, fetches the day's exports (folder, SFTP or URLs),
standardises them, builds the plan and saves it for approval (nothing is released automatically), refreshes transfer
exceptions, purges stored inputs older than the retention period and posts a summary to Teams / Slack / Google Chat. Run it
from the included GitHub Actions workflow (`.github/workflows/daily-plan.yml`, 06:30 IST), cron, or Windows Task Scheduler;
on your own server, `STOCKFLOW_SCHEDULER=on` runs it inside the app. A second trigger on the same day does nothing.

**Background processing and speed.** Plans are built in a background worker: the page shows progress, stays usable, and the
plan is saved even if the browser is closed. Plans are shared between sessions, so many approvers opening the same daily
plan use one copy and it opens instantly. The engine was optimised without changing a single recommendation (verified line
by line against the previous engine):

| Stores | Before | Now | Memory |
|---|---|---|---|
| 9 (demo) | 7.7 s | 2.1 s | — |
| 20 (hackathon data) | 15.0 s | 6.4 s | — |
| 100 | 102.8 s | 22.1 s | ~0.5 GB |
| 200 | 197 s | 51.5 s | ~0.9 GB |
| 500 | 716 s | 139 s | ~1.9 GB (use a 4 GB server) |

**Company sign-in.** Add an `[auth]` section (Microsoft Entra ID, Google Workspace, Okta) and a "Sign in with your company
account" button appears. The work email is matched to a StockFlow user for the role; MFA comes from the identity provider.

**Readiness checks.** `python tests/enterprise_check.py` runs 25 checks across data integration, security, reliability,
auditability and correctness against the configured database (SQLite or PostgreSQL).

## Pilot tools (Nov 2026)

- **Shadow mode** (Operations tab): planners upload what they actually moved (their own sheet: From, To, Item code, Qty);
  StockFlow shows how much of its plan matches, what it would have added (with its estimated value) and what it would not
  have done. Nothing is released. Comparisons can be saved to the pilot record.
- **Pilot scorecard** (Operations tab): pick pilot stores, comparison stores and the start date; the six pass marks
  (in-stock and old stock against comparison stores, transfers complete and on time, planner hours, trips approved without
  major edits, serious data errors left open) are computed from what StockFlow already records.
- **Festival lift**: each named calendar event (Diwali, Eid, a sale) learns its own lift per region from its past
  occurrences, measured against the five weeks before each one (`season.named_events`, `season.event_shrink_days`).
  Back-test on the hackathon pack, 7 festivals in 2026 × regions, week-before-festival units: error 21% → 16%.
- **Your data** (Admin tab, `GET /api/v1/admin/export`): everything in one zip (CSV per table + uploaded files, no password
  or key hashes); delete everything on exit (typed confirmation + password; the deletion itself is audited).
- **Checks on every push** (GitHub Actions): `tests/run_all.py` on SQLite and PostgreSQL, Bandit, pip-audit, npm audit;
  the production image is built and started with the full stack, smoke-tested over HTTPS, the suites run inside it, a
  backup is taken and restored, and an OWASP ZAP baseline scan runs (fails on high-risk findings). The Android app is
  built on every change to `mobile/`.

## Agents (Nov 2026)

Six assistants on the **Agents** tab (and `/api/v1/agents`). They read the plan, transfers and problems and write
**drafts**; nothing is sent, recorded or ordered until a person with the right role approves a draft (it can be edited
first). Approval runs only a fixed list of actions (resolve exceptions, record a dispatch or receipt, send a WhatsApp,
approve a purchase order, post the brief, save a column mapping), each with a role check and an audit entry.

| Agent | What it drafts |
|---|---|
| Morning brief | Per region: trips to approve (urgent first), late transfers, open problems, buy suggestions, the first thing to do |
| Exception investigator | Evidence (transfer lines, counts, files), likely cause, fix at source, a resolution note |
| Transfer follow-up | WhatsApp nudges for late transfers; replies such as "sent all", "received 10, 2 damaged" or "got KUR-01 4" become proposed updates (inbound webhook `POST /api/v1/whatsapp/inbound`, Meta or Twilio, signature checked) |
| Supplier buying | One purchase order per supplier (pack size and minimum order applied) and the email to send |
| Data onboarding | "Check my files first": what each file was read as, guessed column matches, what is missing; mapping suggestions |
| What-if planner | "close KOL-SL", "demand +20% in Dolls", "add a store like PAT-01 called PAT-02", "cover days 21": plans twice and compares |

AI is **off by default**: every agent works from rules and templates. An admin turns AI on in Integrations (needs
`ANTHROPIC_API_KEY` and confirmation that the data-processing agreement covers it); Claude then writes the drafts and can
call read-only lookups (at most 8 steps per run). Model: `STOCKFLOW_AGENT_MODEL` (default `claude-haiku-5-5`, about ₹0.1 a draft). Guests
never see agents. Tests: `python tests/agents_check.py` (14 checks, no network; a fake model client tests the AI path).

## AI spending cap

Every AI feature (agents, Ask answers, reading photos, growth messages) goes through `ai_budget.py`. Default cap: **₹500 a
month**, changeable in Integrations, where this month's spend is shown by feature. Before each call the worst case is reserved;
a call that could pass the cap is not made and the feature uses its rules instead. Costs are counted from the token counts
Anthropic returns, at list prices (₹88 = $1). Models: agents `claude-haiku-5-5`, Ask `claude-sonnet-5-5`
(`STOCKFLOW_CHAT_MODEL`), photos `claude-sonnet-5-5` (`STOCKFLOW_VISION_MODEL`), growth `claude-sonnet-5-5`
(`STOCKFLOW_GROWTH_MODEL`). Also set a spend limit in the Claude Console: the cap cannot see other apps using the same key.

## Growth: the marketing agent (for the team selling StockFlow)

`streamlit run growth_app.py` (admins of the server only). For each prospect: a brief (likely problems, who decides,
objections), an ROI estimate with every assumption shown, a tailored email, WhatsApp, LinkedIn note and call script, a
four-step follow-up sequence, and a **demo data pack built like their business** (`demo_factory.py`: their category, number
of warehouses and stores, their cities, with typical problems planted). A pipeline tracker holds stages and due follow-ups.
It drafts only: you send from your own email or phone. Every message carries an opt-out line; use business contacts only.
Tests: `python tests/growth_check.py`.

## AI answers (optional)

The Ask tab works offline by default. An admin can turn on AI answers with an Anthropic API key after confirming that
sending the plan report is allowed. Only plan figures are sent; customer and staff data is never loaded. Cover this
in the brand's data-processing agreement first.

## Files

`engine.py` planning engine · `schema.py` settings and sheet definitions · `app.py` Streamlit app · `insights.py` ·
`chat.py` · `feedback.py` (run log, ratings, released TOs, receipts in `./workspace`) · `auth.py` · `brands/*.json`
settings profiles · `make_demo_data.py` fictional demo pack.

## Hackathon data pack

The original hackathon pack is still in `data/csv` (it belongs to Vedant Fashions Limited and is not for resale demos).
Run it with: `python engine.py --data data/csv --config brands/sample_ethnic_chain.json --out output`.
`tests/check_plan.py` re-checks a plan against the raw sheets, independently of the engine's own rule checks.
