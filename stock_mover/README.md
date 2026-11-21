# Stock Mover

Daily ready-to-approve list of stock moves for the 20 Manyavar stores and warehouse WH-KOL
(Vedant Fashions AI Hackathon, case study B).

## Setup

```powershell
cd stock_mover
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

On Windows you can double-click `Start Stock Mover.bat` instead. If `pip install anthropic` fails with a
long-path error, the launcher installs it into `stock_mover/_lib`, and the chatbot loads it from there.

The sample data pack (Vibe Coders Hackathon Edition 3) is in `data/`: CSVs in `data/csv`, the two briefs in
`data/briefs`. The data and briefs belong to Vedant Fashions Limited; see `data/README.md`.

## The app (upload new data, get the plan explained)

Double-click **`Start Stock Mover.bat`** (or run `python -m streamlit run app.py`). It opens in your browser.
Upload the data pack (a zip of the CSVs, the Excel workbook, or the CSV files) and press **Run Stock Mover**.

| Tab | Shows |
|---|---|
| ⭐ Leaders | Key insights only: 4 headline numbers, up to 5 insights, decisions needed, impact by city |
| 📋 Plan | Today in plain words, moves by type, where stock goes (city map + store flow diagram), area-manager alerts |
| 🔍 Problems | Each problem in plain language (what, why it matters, what was done) with its own chart |
| 🏬 Stores | Pick a store: stock vs minimum display, how long stock lasts, what comes in and goes out |
| ✅ Approve | Tick trips to approve and download the approved list |
| 💬 Ask | Chatbot that answers questions about this report only and apologises for anything else |
| 📝 Feedback | Rate past plans, ask for changes, see rating trends, accept suggested settings for future runs |
| ℹ️ Method | Diagram of the method, glossary, rule checks, season effect found in the data |

It works on phones too: the layout stacks into one column and the tabs scroll sideways.
If a table or column is missing, the app lists exactly what to fix.

**Chatbot.** With an Anthropic API key (set `ANTHROPIC_API_KEY`, or paste it on the Ask tab for that browser
session) it uses Claude Opus 5.5. The whole report is sent once and cached, and server-side refusal fallback is on.
Without a key it uses built-in quick answers for common questions (stores, items, approvals, rules, problems,
warehouse, old stock). Either way, questions outside the report get an apology.

**Feedback.** Every run is logged to `feedback/runs.csv` and ratings to `feedback/ratings.csv`. Requested changes are
netted into one-step setting suggestions; accepting them writes `feedback/settings.json`, which the next run uses.
Delete that file, or press Reset, to go back to standard settings.

## Run from the command line

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily.ps1
```

or `python stock_mover.py --data <csv folder> --out <output folder>`. Needs Python 3.11+ with `pandas` and `openpyxl`.
The plan date is the day after the latest `as_of_date` in `stock_snapshot`, so dropping in a new data
export is all it takes to produce the next day's list. The run exits with an error if any rule check fails.

## Output (`output/`)

`stock_mover_<date>.xlsx`:

| Sheet | For |
|---|---|
| Summary | Totals by move type, rule-check score, data issues count |
| Routes | One line per from -> to route with an approve column: the 10-minute approval view |
| Moves | Every SKU move with qty, value, dispatch and arrival dates, priority, approval flag and a one-line reason |
| Data issues | Problems found in the data and what the plan did about each |
| AM alerts | At most 5 alerts per area manager (their weekly reading limit) |
| Rule checks | Every rule re-checked independently against the final plan |
| Velocity | Per store x SKU velocity, forecast, stock, cover and need, so any number can be spot-checked |

CSV copies of moves, data issues and rule checks are written alongside.

## How it decides

1. **Clean.** Packaging rows are dropped. Snapshot stock is reconciled with the 31 Oct weekly count;
   on conflict the weekly count wins and the SKU is flagged (KNP-03 SHW-02-L: 300 vs 0; MYS-02 KUR-02-M: -4 vs 3).
2. **Velocity** = units sold / days the size was actually in stock (`days_in_stock`), last 12 weeks,
   recent weeks weighted more. Each store x SKU is blended with a prior (chain in-stock rate x the store's
   strength in that category), so zero sales because of zero stock does not read as zero demand,
   and new stores and launches borrow from similar stores.
3. **Season.** Each state's history gives the lift of wedding dates and festival weeks over normal days;
   the calendar for the coming weeks scales velocity up or down.
4. **Plan.**
   - A. **Warehouse -> store.** Each size is topped up to lead time + 28 days of forecast demand,
     counting stock in transit. When the warehouse is short, units go one at a time to the store
     with the lowest cover. Inbound due within a day is used too.
   - **Min display.** Topped up from the warehouse, or from stores that can spare it.
   - B. **Store -> store.** Fills what the warehouse can't, nearest donor first. A donor is never
     an S++ store giving a protected style, never left under 30 days of style cover, never pushed
     below minimum display, and never a store receiving that size today.
   - C. **Ageing.** Stock older than 120 days, with over 60 days of cover, moves to stores that will
     sell it within 45 days.
   - D. **Pull back.** Aged stock that still has over 120 days of cover, and that no store can sell,
     returns to the warehouse. One of each size stays on display unless the size didn't sell anywhere
     in 12 weeks despite being on shelves.
5. **Validate.** All rules are re-checked from scratch on the final plan.

Settings are in `CFG` at the top of `stock_mover.py`.
