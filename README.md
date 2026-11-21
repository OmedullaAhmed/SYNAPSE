# Synapse

## Stock Mover: Manyavar AI Hackathon, case study B

### 🔗 Live app: https://synapse-stock-mover.streamlit.app
Open it and press **Try the sample data**. A plan takes about 30 seconds. If the page shows a "wake up" button,
click it and wait about 30 seconds while the free server starts.

**[stock_mover/](stock_mover/)** finds stock in the wrong place every day and writes a ready-to-approve list:
what to send from the warehouse, what to move between stores, and what to pull back. Every move comes with a
one-line reason, and every business rule is re-checked on the final plan.

- **App:** upload a data pack and get the plan with plain-language problems, charts, a leadership view, a
  report chatbot and a feedback loop. Works on phones too.
- **Engine:** `stock_mover/stock_mover.py` runs the whole pipeline from the command line.
- **Sample data and briefs:** `stock_mover/data/`.
- **Sample result:** `stock_mover/sample_output/stock_mover_2026-11-01.xlsx` (1,166 moves, all 11 rules passed, 26 data issues found).

```powershell
cd stock_mover
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

See [stock_mover/README.md](stock_mover/README.md) for how it decides.

## Web app starter

A website and web app built with React, TypeScript and Vite.

## Getting started

```bash
npm install
npm run dev
```

Then open http://localhost:5173.

## Scripts

- `npm run dev`: start the dev server
- `npm run build`: type-check and build for production (output in `dist/`)
- `npm run preview`: preview the production build
