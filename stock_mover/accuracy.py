"""Forecast accuracy back-test: how well would the plan's selling-speed forecast have predicted the last few weeks?

    rep = accuracy.backtest(std_dir, cfg, horizon=28)

1. Cut the data off `horizon` days before the latest stock date (sales and weekly in-stock records stop there).
2. Run the planner on that cut-off data: its forecast per store x SKU (stockout-corrected speed x season) is what it
   would have planned with.
3. Compare with what actually sold in the next `horizon` days, only where the item was in stock most of that time
   (a shelf with nothing on it sells nothing whatever the forecast said).
4. Do the same for the simple method most chains use today: the average of the last 28 days, no corrections.

Measures: WAPE = sum |forecast - actual| / sum actual (lower is better; 0.3 = 30% error) and bias = sum(forecast - actual)
/ sum actual (positive = over-forecast), at store x SKU, store x category and chain x category level.
"""
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import engine as E


def _wape(f, a):
    return float(np.abs(f - a).sum() / a.sum()) if a.sum() > 0 else float("nan")


def _bias(f, a):
    return float((f - a).sum() / a.sum()) if a.sum() > 0 else float("nan")


def backtest(std_dir, cfg, horizon=28, min_in_stock=0.8):
    std_dir = Path(std_dir)
    if (std_dir / "sales.csv").exists():
        sales = pd.read_csv(std_dir / "sales.csv", dtype=str, keep_default_na=False)
    else:                                               # older layout: bills + bill_lines
        b = pd.read_csv(std_dir / "bills.csv", dtype=str, keep_default_na=False).rename(columns={"bill_date": "date"})
        bl = pd.read_csv(std_dir / "bill_lines.csv", dtype=str, keep_default_na=False)
        sales = bl.merge(b[["bill_id", "date", "store_id"]], on="bill_id")[["date", "store_id", "sku_id", "qty"]]
    snap = pd.read_csv(std_dir / "stock_snapshot.csv", dtype=str, keep_default_na=False)
    sales["d"] = pd.to_datetime(sales.date, errors="coerce")
    sales["q"] = pd.to_numeric(sales.qty, errors="coerce").fillna(0)
    end = pd.to_datetime(snap.as_of_date, errors="coerce").max()
    cut = end - pd.Timedelta(days=horizon)
    if sales.d.min() > cut - pd.Timedelta(days=56):
        raise ValueError(f"Not enough history: need sales from at least {(cut - pd.Timedelta(days=56)):%d %b %Y}.")
    work = Path(tempfile.mkdtemp(prefix="stockflow_acc_"))
    try:
        for f in std_dir.glob("*.csv"):
            shutil.copy(f, work / f.name)
        sales[sales.d <= cut].drop(columns=["d", "q"]).to_csv(work / "sales.csv", index=False)
        for f in ("bills.csv", "bill_lines.csv"):          # sales.csv above replaces them
            (work / f).unlink(missing_ok=True)
        snap.assign(as_of_date=cut.strftime("%Y-%m-%d")).to_csv(work / "stock_snapshot.csv", index=False)
        wk = None
        if (std_dir / "stock_weekly.csv").exists():
            wk = pd.read_csv(std_dir / "stock_weekly.csv", dtype=str, keep_default_na=False)
            wk["we"] = pd.to_datetime(wk.week_ending, errors="coerce")
            wk[wk.we <= cut].drop(columns="we").to_csv(work / "stock_weekly.csv", index=False)
        R = E.run(work, None, cfg)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    g = R["pl"].g.reset_index()[["store_id", "sku_id", "dcat", "fd30"]]
    g = g[~g.store_id.isin(R["pl"].outlets)]
    act = sales[(sales.d > cut) & (sales.d <= end)].groupby(["store_id", "sku_id"]).q.sum().clip(lower=0).rename("actual")
    base = sales[(sales.d > cut - pd.Timedelta(days=28)) & (sales.d <= cut)].groupby(["store_id", "sku_id"]).q.sum().clip(lower=0) / 28
    d = g.merge(act, left_on=["store_id", "sku_id"], right_index=True, how="left").fillna({"actual": 0})
    d["forecast"] = d.fd30 * horizon
    d["naive"] = d.set_index(["store_id", "sku_id"]).index.map(base).fillna(0).values * horizon
    if wk is not None and len(wk):                      # score only what was on the shelf most of the horizon
        wk["days"] = pd.to_numeric(wk.days_in_stock, errors="coerce").fillna(0)
        on = wk[(wk.we > cut) & (wk.we <= end)].groupby(["store_id", "sku_id"]).days.sum()
        weeks = wk[(wk.we > cut) & (wk.we <= end)].we.nunique()
        if weeks:
            d["in_stock"] = d.set_index(["store_id", "sku_id"]).index.map(on).fillna(0).values / (7 * weeks)
            d = d[d.in_stock >= min_in_stock]
    else:                                               # no in-stock records: score only items the store carries
        recent = sales[(sales.d > cut - pd.Timedelta(days=56)) & (sales.d <= end) & (sales.q > 0)]
        ranged = set(zip(recent.store_id, recent.sku_id))
        d = d[[k in ranged for k in zip(d.store_id, d.sku_id)]]
    d = d[(d.forecast > 0) | (d.actual > 0) | (d.naive > 0)]
    levels = {}
    for name, keys in (("Store × SKU", ["store_id", "sku_id"]), ("Store × category", ["store_id", "dcat"]),
                       ("Chain × category", ["dcat"])):
        x = d.groupby(keys)[["forecast", "naive", "actual"]].sum()
        levels[name] = dict(stockflow_wape=_wape(x.forecast, x.actual), simple_wape=_wape(x.naive, x.actual),
                            stockflow_bias=_bias(x.forecast, x.actual), simple_bias=_bias(x.naive, x.actual), rows=len(x))
    cat = d.groupby("dcat").apply(lambda x: pd.Series(dict(actual=x.actual.sum(), forecast=x.forecast.sum(),
                                                            stockflow_wape=_wape(x.forecast, x.actual),
                                                            simple_wape=_wape(x.naive, x.actual))), include_groups=False)
    return dict(cutoff=cut.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), horizon=horizon, pairs=len(d),
                actual_units=float(d.actual.sum()), levels=levels, by_category=cat.reset_index().rename(columns={"dcat": "category"}))


def summary_frame(rep):
    rows = [dict(level=k, stockflow_error=v["stockflow_wape"], simple_average_error=v["simple_wape"],
                 stockflow_bias=v["stockflow_bias"], simple_average_bias=v["simple_bias"], compared=v["rows"])
            for k, v in rep["levels"].items()]
    return pd.DataFrame(rows)


def for_inputs(src, cfg, horizon=28):
    """Back-test from raw input files (any POS/ERP export layout): standardise first, then backtest."""
    import ingest
    work = Path(tempfile.mkdtemp(prefix="stockflow_acc_in_"))
    try:
        ingest.run(src, work, cfg=cfg, log=False, stale=False)
        return backtest(work, cfg, horizon)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def save(plan_id, rep, user=""):
    import db
    keep = {k: v for k, v in rep.items() if k != "by_category"}
    keep["by_category"] = rep["by_category"].to_dict("records")
    db.upsert_setting(f"acc:{plan_id}"[:40], keep, user)


def load(plan_id):
    import db
    r = db.get_setting(f"acc:{plan_id}"[:40])
    if r:
        r["by_category"] = pd.DataFrame(r.get("by_category", []))
    return r
