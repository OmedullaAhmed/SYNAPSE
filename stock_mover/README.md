# StockFlow: daily stock moves for multi-store apparel brands

Upload yesterday's sales and stock and get back a short list of **trips** to approve. Each trip has a one-line
reason, what it gains and what it costs to move. StockFlow is built for ethnic and occasion wear brands with about
5 to 100 stores, one or more warehouses and optional outlet stores. It contains no brand names, store grades,
category names or warehouse codes; everything comes from your sheets and an optional `config.json`.

```
pip install -r requirements.txt
python make_demo_data.py            # builds the fictional "Demo Threads" pack in data/demo
streamlit run app.py                # press "Try the demo brand", or upload your own pack
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

**Sign-in is mandatory.** With no users anywhere, the first visitor creates the admin account. Admins add users in the
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
