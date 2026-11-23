# What changed from the hackathon Stock Mover

Before and after numbers come from the same hackathon data pack (20 stores, 1 warehouse, plan for 1 Nov 2026).
The "before" numbers come from the original app's own sample output.

| Downside | Fix | Before → after |
|---|---|---|
| Too many tiny moves | Lines are grouped into trips, and every trip pays a fixed dispatch cost. Lines and trips that cost more than they earn are dropped. Store-to-store trips under 4 pieces are held back unless urgent. Donors that already have a trip to the same store are preferred, and each store packs at most 3 trips a day. | 1,288 lines on 186 routes → **71 trips** (938 lines) |
| No cost of movement | Freight per piece and per trip comes from `lanes.csv` (any from/to: id, city, zone, type), with defaults by route type. Handling per piece is added. | Every line and trip shows gain, cost and net |
| No opportunity cost | Each line is valued as Poisson-expected extra sales × real margin (after discount, tax and cost), minus sales the giving store loses, plus markdown and holding cost avoided on old stock. The Money tab shows what not acting would cost, what is still at risk where no stock exists, and the monthly cost of old stock left in stores. | Model estimate: ₹69.7 L gain for ₹2.3 L moving cost; ₹71.7 L margin still at risk; old stock costs ~₹8.5 L/month |
| Sizes moved one at a time, breaking size sets | Core sizes come per category from sales, or from `size_sets.csv` / config. Options on display that are missing a core size get it first. A giving store never gives its last core size of a selling option. Broken runs of old stock move together to one store that sells the option and lacks those sizes, or come back together. Broken sets left after the plan are reported to buying. | New reasons: "Complete broken size sets", "Reunite broken size runs" |
| Too much pulled back in one go | Returns are capped per store per run: 25 pieces, 5% of stock and ₹1.5 L at MRP (all configurable). The most valuable and oldest go first; the rest is queued for later runs. | 353 pcs / ₹57.3 L in one day → **151 pcs / ₹26.3 L**, at most ₹1.5 L per store; 235 pcs queued |
| No colour, fit, returns, damage or season end | Colour and fit are optional item columns; option = style + colour + fit. `stock_status` keeps alteration, damaged and return-QC stock out of the plan and reports it. `season_end` stops refills that would land after the season. Leftover stock goes to an outlet (`store_type = outlet`) or the warehouse. | New "Clear end-of-season / dead stock" reason; outlets supported |
| Approval was only a CSV | One transfer order per trip in a generic ERP/WMS import format. "Release" logs the orders, and a receipts upload gives variance and late/short status per line on the Track tab. | Approve → release → track |
| No login; data and API privacy | Sign-in with hashed passwords and three roles (viewer / approver / admin), optionally limited to certain stores. An "open demo mode" warning shows when no users are set. Customer and staff columns are dropped on load. AI answers are off by default; an admin turns them on after confirming the data-processing agreement allows it. | |
| Hardcoded for one brand | All brand names, tiers (S++…), categories, the single warehouse and the lead-time sentences are gone. Any number of warehouses, any tier labels (order set in config), category display groups, currency and number style come from settings. Protected styles apply to any store listed. Old sheet and column names are still read. | Same engine runs the fictional demo brand and the hackathon pack |
| Similar reasons repeated line by line | Every line has one of 9 broad reasons. Each trip gets a single clubbed line, e.g. "Refill before it runs out (84 pcs), stock up for the peak ahead (44 pcs), complete broken size sets (2 pcs). Gains ₹3.1 L for ₹7,950 of moving cost." Routine approvals are one decision per reason. | 26 decisions for the whole day |

All 17 rule checks pass on both packs. On the hackathon pack, an independent recheck against the raw CSVs found no store
giving more than it physically holds, no warehouse over-sending, and no protected style given away.

## Not done (needs a live brand)
- Direct API push into a specific ERP or WMS. The CSV format is generic; each ERP needs its own connector.
- Calibrating the economics (holding rates, markdown per month, recovery uplift) against a brand's actual results.
- Replacing the hackathon sample pack: that data belongs to Vedant Fashions and is not in this package. Use the fictional demo pack for sales demos.

## Enterprise release (Nov 2026)

Added without removing anything: PostgreSQL storage (SQLite fallback) for plans, recommendations, line decisions,
released orders, movements, settings, users and audit; mandatory sign-in with database users, a fourth "store" role,
a permission matrix, lockout, idle timeout and forced password change; an append-only audit trail with an Audit tab;
line-level approve, reject and quantity edits feeding transfer orders; a chain-wide Inventory dashboard with store
comparison; plans saved with their inputs so they can be reopened after a restart, with tracking kept in the database.

## Operations release (Nov 2026)

Added without removing anything: standardised ingestion of POS / stock / product-master exports with mapping profiles and
data-quality checks (ingest.py); ERP/WMS exchange by folder, SFTP or API with idempotent confirmations (erp.py); daily
automation with GitHub Actions, cron or an in-app scheduler (daily.py); an exceptions queue for data problems and transfer
discrepancies (exceptions_queue.py); regional approval queues and transfer status by region (Operations tab); background
jobs with progress (jobs.py) through one shared pipeline (pipeline.py); a 3.6–5x faster engine with identical output;
shared plan cache; company sign-in (OIDC); spreadsheet-formula neutralising on every export (safety.py); HTML escaping of
data in the page; retention purge of stored inputs; Teams/Slack/Google Chat alerts; tests/enterprise_check.py.

## Any-business release (Nov 2026)

Daily scheduled run paused (manual trigger only). Added without removing anything: optional sizes/styles/colours, expiry
dates, six business-type profiles and a general-retail demo; stock from photos and scans (barcodes, OCR, opt-in AI) with
reviewed counts that feed plans and exceptions; REST API with tokens, API keys, scopes and signed webhooks; Android / iOS /
web phone app with offline outbox; approval deadlines with escalation; purchase suggestions; forecast accuracy back-test;
bulk user import; Docker / compose / HTTPS / backup-restore kit; optional error tracking; outbound URL safety; security
overview and a data-protection draft. Fixed: engine crash on stock without dates and nothing old; pack_size column
swallowed by a size alias; Integrations tab crash without a secrets file; age bands for short "old stock" thresholds;
store users seeing other stores' exceptions.

## Forecast release (Nov 2026)

Selling speed now blends the last 4 weeks with the longer average and carries category trends forward; items that sell
before their launch date are forecast (and reported). Fixed a planner gap where an old-stock "whole run" move could leave
a still-selling store without a core size or under its style cover when combined with a refill (rules 14 and 13). Plans
change slightly as a result; all 17 rules pass on every dataset, including 100, 200 and 500 stores.


## Agents and growth (23 Nov 2026)

- Six operations agents (morning brief, exception investigator, transfer follow-up with WhatsApp replies, supplier buying,
  data onboarding, what-if planner). Draft-and-approve only; whitelisted actions; role checks; audit; AI off by default
  with rule-based drafts; Claude tool use with read-only tools when an admin turns AI on.
- WhatsApp inbound webhook (Meta signature `X-Hub-Signature-256` with `app_secret`, Twilio `X-Twilio-Signature` with
  `auth_token` and `inbound_url`); unsigned requests are refused; replies only create drafts.
- Growth app for the sales team: prospect brief, ROI estimate, tailored messages, follow-ups, personalised demo data, pipeline.
- Fixed: buy suggestions, KPI history and the product catalog were silently not saved when a plan was built in a background
  job from the web app (Streamlit restores `sys.path` after each run, so first-time imports inside the job failed).
  These modules are now imported up front.
- Fixed: a PostgreSQL URL using a Unix socket (`?host=/path`) was treated as the example placeholder.
- Fixed: two plans for the same date reused transfer numbers, so releasing both mixed their tracking. Numbers now carry a
  3-character plan tag taken from the input files and settings (TO-261123-A7F-001): different plans get different
  numbers, rebuilding the same plan gives the same ones. Releasing a number that is already released is skipped and
  reported (web app, API). Old-style numbers (TO-261123-001) are still read.

- AI spending cap shared by all AI features (default ₹500 a month, Integrations tab); calls that could pass it are not made.
  Agents now default to Claude Haiku 5.5; Ask answers moved from Opus 5.5 to Sonnet 5.5.

## Pilot readiness (23 Nov 2026)

- Shadow mode, pilot scorecard, per-festival forecast lift, full data export and delete-on-exit (see README).
- Automated checks on GitHub for every push: all suites on SQLite and PostgreSQL, security scans, the production stack
  built and tested end to end (HTTPS, sign-in, PostgreSQL, backup and restore, OWASP ZAP), Android build.
- Fixed: when `deploy/secrets.toml` still had the example database URL, the app ignored the server's `DATABASE_URL` and
  used a temporary SQLite file. It now falls back to `DATABASE_URL` first.
