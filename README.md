<p align="center">
  <img src=".github/assets/banner.svg" alt="Stock Mover by Synapse, prepared for Manyavar and Mohey" width="100%">
</p>

<p align="center">
  <a href="https://synapse-stock-mover.streamlit.app"><img alt="Live app" src="https://img.shields.io/badge/live%20app-open-8b1a2b?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-streamlit-8a6a4f?style=flat-square">
  <img alt="React" src="https://img.shields.io/badge/web-react%20%2B%20vite-4a0e1c?style=flat-square">
  <img alt="Rules" src="https://img.shields.io/badge/rule%20checks-11%2F11%20passed-e8a317?style=flat-square">
  <img alt="Hackathon" src="https://img.shields.io/badge/manyavar%20AI%20hackathon-case%20B-d1495b?style=flat-square">
</p>

<p align="center">
  <a href="#stock-mover">Stock Mover</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#repository-layout">Layout</a> ·
  <a href="#web-app">Web app</a>
</p>

---

## Stock Mover

**Stock in the right store, every morning.** Stock Mover reads a retailer's sales, stock and delivery data and
writes a ready-to-approve list of moves: what to send from the warehouse, what to move between stores, and what to
pull back. Every move has a one-line reason, and every business rule is re-checked on the final plan.

<table>
  <tr>
    <td width="33%" valign="top"><b>📋 Plan</b><br>Warehouse sends, store-to-store moves and pull-backs, each with its reason.</td>
    <td width="33%" valign="top"><b>🔍 Explain</b><br>Data problems in plain language, with a chart and what was done about each one.</td>
    <td width="33%" valign="top"><b>✅ Approve</b><br>Tick the trips to approve, download the list, and rate the plan to tune future runs.</td>
  </tr>
</table>

**Try it:** open the [live app](https://synapse-stock-mover.streamlit.app) and press **Try the sample data**.
A plan takes about 30 seconds. If the page asks you to wake it up, click the button and wait about 30 seconds
while the free server starts.

| Sample run (2026-11-01) | |
|---|---|
| Moves planned | **1,166** |
| Business rules passed | **11 of 11** |
| Data issues found | **26** |

## Quick start

```bash
cd stock_mover
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

On Windows you can double-click `stock_mover/Start Stock Mover.bat` instead. The full method is in
[stock_mover/README.md](stock_mover/README.md).

## Repository layout

```text
SYNAPSE/
├── stock_mover/          Stock Mover app and engine (Python, Streamlit)
│   ├── app.py            the web app
│   ├── stock_mover.py    the planning engine, also runs from the command line
│   ├── data/             sample data pack and briefs
│   └── sample_output/    a finished plan (Excel and CSV)
├── src/                  Synapse website (React, TypeScript, Vite)
└── .github/assets/       logo and banner
```

## Web app

The Synapse website lives at the repo root.

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # production build in dist/
```

## Brand

Styled for **Manyavar · Mohey**: festive gold drawn from the Manyavar wordmark, deep maroon, ivory backgrounds and
Cormorant Garamond serif headings, with gold ornament dividers.

| Token | Color | Used for |
|---|---|---|
| Deep maroon | `#4a0e1c` | hero backgrounds, headers |
| Maroon | `#8b1a2b` | primary buttons, warehouse sends |
| Gold | `#e8a317` | accents, store-to-store moves |
| Saffron | `#e07020` | aged stock, gradient end |
| Crimson | `#d1495b` | alerts, pull-backs |
| Plum | `#6d3a7d` | display fills |
| Ivory | `#fdf9f3` | page background |

---|---|---|
| Deep | `#4a0e1c` | backgrounds, headers |
| Teal | `#8b1a2b` | primary actions, warehouse sends |
| Gold | `#e8a317` | highlights, store-to-store moves |
| Sand | `#e07020` | aged stock |
| Coral | `#d1495b` | alerts, pull-backs |

---

<p align="center"><sub>Built for the Vedant Fashions (Manyavar) AI Hackathon. Sample data and briefs belong to Vedant Fashions Limited.</sub></p>
