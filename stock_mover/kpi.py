"""Stock KPIs: today's numbers, the plan's predicted effect, and predicted-vs-actual sales over time.

Three sources:
  today        from the open plan: stock, cover, in-stock rate, stockout risk, old stock, sell-through, turns
  replay       predicted vs actual for each of the last N weeks, rebuilt from the plan's own data: the forecast is
               recomputed as it would have been at the start of each week and compared with what then sold
               (and with the simple "last 4 weeks' average" most chains use). Available from the first upload.
  history      every saved plan stores its KPIs and its 7-day forecast by store x category (kpi_history); when later
               data arrives, the forecast is checked against what actually sold. Builds up as plans are saved daily.
"""
from datetime import datetime

import numpy as np
import pandas as pd

import engine as E

DEF = {
    "stock_units": "Units on hand in stores and warehouses (sellable only).",
    "cover_days": "Store stock ÷ forecast daily sales: how many days the stores' stock lasts at the expected selling speed.",
    "in_stock": "Share of store × item pairs that sell (forecast > 0) and have at least one unit on the shelf.",
    "at_risk": "Selling store × item pairs whose stock runs out before the next delivery could arrive.",
    "old_share": "Share of store units older than the 'old stock' age for this business type (or close to expiry).",
    "sell_through": "Units sold in the last 30 days ÷ (units sold + units on hand now), stores only.",
    "turns": "Cost of goods sold in the last 90 days × 4 ÷ stock at cost: how many times a year stock turns over.",
    "pred7": "Units the stores would sell in the next 7 days if every item were on the shelf (selling speed × season).",
    "wape": "Weighted error: total |predicted − actual| ÷ actual units. 0.2 = 20% off. Lower is better.",
    "bias": "(predicted − actual) ÷ actual. Positive = predicted too high, negative = too low.",
}


# ----------------------------------------------------------------------------- today
def _final_pos(R):
    pl, mv = R["pl"], R["moves"]
    fin = pl.g.pos.copy()
    for (k, q) in mv[mv.to_id.isin(pl.st.index)].groupby(["to_id", "sku_id"]).qty.sum().items():
        fin[k] += q
    for (k, q) in mv[mv.from_id.isin(pl.st.index)].groupby(["from_id", "sku_id"]).qty.sum().items():
        fin[k] -= q
    return fin


def today(R):
    """Chain KPIs now and (where the plan changes them) after today's plan. Returns (dict, per-store frame)."""
    pl, P, cfg = R["pl"], R["P"], R["cfg"]
    g = pl.g.reset_index()
    g = g[~g.store_id.isin(pl.outlets)]
    it = pl.it
    fin = _final_pos(R).reindex(pl.g.index).values
    g["after"] = pd.Series(fin, index=pl.g.index).reindex(list(zip(g.store_id, g.sku_id))).values
    sell = g.fd30 > 0
    lead_need = g.fd * g.L
    snap = P["snap"]
    whs = snap[snap.location_id.isin(pl.whs) & snap.ok] if "ok" in snap else snap[snap.location_id.isin(pl.whs)]
    cost = g.sku_id.map(it.cost).astype(float)
    s = P["sales"]
    end = pd.Timestamp(P["today"])
    s30 = s[(s.date > end - pd.Timedelta(days=30)) & s.store_id.isin(g.store_id.unique())]
    s90 = s[(s.date > end - pd.Timedelta(days=90))]
    sold30 = float(s30.qty.clip(lower=0).sum())
    cogs90 = float((s90.qty.clip(lower=0) * s90.sku_id.map(it.cost).astype(float)).sum())
    stock_cost = float((g.oh * cost).sum() + (whs.oh * whs.sku_id.map(it.cost).astype(float)).sum())
    old = g.age >= cfg["ageing"]["age_days"]
    k = dict(
        stock_units=int(g.oh.sum() + whs.oh.sum()), store_units=int(g.oh.sum()), warehouse_units=int(whs.oh.sum()),
        stock_cost=stock_cost, stock_mrp=float((g.oh * g.mrp).sum() + (whs.oh * whs.sku_id.map(it.mrp)).sum()),
        cover_days=float(g.pos.sum() / g.fd30.sum()) if g.fd30.sum() > 0 else float("nan"),
        cover_days_after=float(g.after.sum() / g.fd30.sum()) if g.fd30.sum() > 0 else float("nan"),
        in_stock=float((g[sell].pos > 0).mean()) if sell.any() else float("nan"),
        in_stock_after=float((g[sell].after > 0).mean()) if sell.any() else float("nan"),
        at_risk=int((sell & (g.pos < lead_need)).sum()), at_risk_after=int((sell & (g.after < lead_need)).sum()),
        old_share=float(g[old].oh.sum() / max(g.oh.sum(), 1)),
        sell_through=sold30 / (sold30 + g.oh.sum()) if sold30 + g.oh.sum() > 0 else float("nan"),
        turns=cogs90 * 4 / stock_cost if stock_cost > 0 else float("nan"),
        pred7=float((g.fd30 * 7).sum()), sold7=float(s[(s.date > end - pd.Timedelta(days=7))].qty.clip(lower=0).sum()),
        as_of=end.strftime("%Y-%m-%d"))
    st = g.groupby("store_id").agg(units=("oh", "sum"), after=("after", "sum"), fd=("fd30", "sum"),
                                   selling=("fd30", lambda x: int((x > 0).sum())))
    st["in_stock"] = g[sell].groupby("store_id").pos.apply(lambda x: (x > 0).mean())
    st["in_stock_after"] = g[sell].groupby("store_id").after.apply(lambda x: (x > 0).mean())
    st["at_risk"] = g[sell & (g.pos < lead_need)].groupby("store_id").size()
    st["at_risk_after"] = g[sell & (g.after < lead_need)].groupby("store_id").size()
    st["cover_days"] = st.units / st.fd.replace(0, np.nan)
    st["cover_days_after"] = st.after / st.fd.replace(0, np.nan)
    st["old_share"] = g[old].groupby("store_id").oh.sum() / st.units.replace(0, np.nan)
    st["pred7"] = st.fd * 7
    st["sold30"] = s30.groupby("store_id").qty.sum()
    st = st.fillna({"at_risk": 0, "at_risk_after": 0, "sold30": 0, "old_share": 0}).drop(columns=["fd", "after"])
    return k, st.reset_index()


# ----------------------------------------------------------------------------- replay: predicted vs actual by week
def _truncate(D, cut):
    D2 = dict(D)
    for t in ("sales", "stock_weekly"):
        if t in D and len(D[t]):
            col = "date" if t == "sales" else "week_ending"
            d = pd.to_datetime(D[t][col], errors="coerce")
            D2[t] = D[t][d <= cut].copy()
    sn = D["stock_snapshot"].copy()
    sn["as_of_date"] = cut if pd.api.types.is_datetime64_any_dtype(sn["as_of_date"]) else cut.strftime("%Y-%m-%d")
    D2["stock_snapshot"] = sn
    return D2


def forecast_at(D, cfg, cut, horizon=7):
    """Units each store x item is expected to sell in the `horizon` days after `cut` (forecast as of that day)."""
    D2 = _truncate(D, cut)
    P = E.prep(D2, cfg)
    vel, _ = E.velocity(D2, P)
    season = E.Season(D2, P)
    pl = E.Planner(D2, P, vel, season)
    g = pl.g.reset_index()
    g = g[~g.store_id.isin(pl.outlets)]
    mult = {r: season.mult(r, pl.run, horizon) for r in g.region.unique()}
    g["pred"] = np.where(g.eoss, 0.0, g.v * g.region.map(mult)) * horizon
    return g[["store_id", "sku_id", "dcat", "pred"]]


def replay(src, cfg, weeks=8, progress=None):
    """Predicted (StockFlow and simple average) vs actual units for each of the last `weeks` weeks, by store x category."""
    say = progress or (lambda *_: None)
    D = E.load(src, cfg)
    sales = D["sales"].copy()
    sales["d"] = pd.to_datetime(sales.date, errors="coerce")
    sales["q"] = pd.to_numeric(sales.qty, errors="coerce").fillna(0).clip(lower=0)
    end = pd.to_datetime(D["stock_snapshot"].as_of_date, errors="coerce").max()
    first = sales.d.min()
    wk = None
    if "stock_weekly" in D and len(D["stock_weekly"]):
        wk = D["stock_weekly"].copy()
        wk["we"] = pd.to_datetime(wk.week_ending, errors="coerce")
        wk["days"] = pd.to_numeric(wk.days_in_stock, errors="coerce").fillna(0)
    weeks = max(1, min(weeks, int(((end - first).days - 56) // 7)))
    out = []
    for i in range(weeks, 0, -1):
        cut = end - pd.Timedelta(days=7 * i)
        say(f"Week starting {(cut + pd.Timedelta(days=1)):%d %b}", (weeks - i) / weeks)
        f = forecast_at(D, cfg, cut)
        win = sales[(sales.d > cut) & (sales.d <= cut + pd.Timedelta(days=7))]
        act = win.groupby(["store_id", "sku_id"]).q.sum().rename("actual")
        base = sales[(sales.d > cut - pd.Timedelta(days=28)) & (sales.d <= cut)].groupby(["store_id", "sku_id"]).q.sum() / 4
        x = f.merge(act, left_on=["store_id", "sku_id"], right_index=True, how="outer")
        x["dcat"] = x.dcat.fillna(x.sku_id.map(f.drop_duplicates("sku_id").set_index("sku_id").dcat)).fillna("Other")
        x["simple"] = [base.get((s, k), 0.0) for s, k in zip(x.store_id, x.sku_id)]
        x = x.fillna({"pred": 0.0, "actual": 0.0})
        x = x[x.store_id.isin(f.store_id.unique())]
        if wk is not None:                         # demand vs sales is only fair where the item was on the shelf
            w_ = wk[(wk.we > cut) & (wk.we <= cut + pd.Timedelta(days=7))]
            on = set(zip(w_[w_.days >= 5].store_id, w_[w_.days >= 5].sku_id))
            x = x[[k in on for k in zip(x.store_id, x.sku_id)]]
        else:                                      # no in-stock records: only items the store carries (sold in 8 weeks)
            rec = sales[(sales.d > cut - pd.Timedelta(days=56)) & (sales.d <= cut + pd.Timedelta(days=7)) & (sales.q > 0)]
            carried = set(zip(rec.store_id, rec.sku_id))
            x = x[[k in carried for k in zip(x.store_id, x.sku_id)]]
        agg = x.groupby(["store_id", "dcat"], as_index=False)[["pred", "simple", "actual"]].sum()
        agg["week_start"] = (cut + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        out.append(agg)
    say("Done", 1.0)
    return pd.concat(out, ignore_index=True).rename(columns={"dcat": "category"})


def weekly(rep, store=None, category=None):
    d = rep
    if store:
        d = d[d.store_id.isin(store)]
    if category:
        d = d[d.category.isin(category)]
    w = d.groupby("week_start", as_index=False)[["pred", "simple", "actual"]].sum()
    w["error"] = (w.pred - w.actual).abs() / w.actual.replace(0, np.nan)
    w["simple_error"] = (w.simple - w.actual).abs() / w.actual.replace(0, np.nan)
    w["bias"] = (w.pred - w.actual) / w.actual.replace(0, np.nan)
    return w


def wape(d, col="pred"):
    a = d.actual.sum()
    return float((d[col] - d.actual).abs().sum() / a) if a > 0 else float("nan")


# ----------------------------------------------------------------------------- history across saved plans
def save_snapshot(plan_id, R, user=""):
    """Store today's KPIs and the 7-day forecast by store x category for this plan (called on every plan build)."""
    import db
    pl = R["pl"]
    g = pl.g.reset_index()
    g = g[~g.store_id.isin(pl.outlets)]
    sell = g.fd30 > 0
    rows = g.assign(sell=sell, instock=(g.pos > 0) & sell, risk=sell & (g.pos < g.fd * g.L),
                    old=(g.age >= R["cfg"]["ageing"]["age_days"]) * g.oh) \
        .groupby(["store_id", "dcat"], as_index=False).agg(on_hand=("oh", "sum"), pred7=("fd30", lambda x: float(x.sum() * 7)),
                                                         selling=("sell", "sum"), in_stock=("instock", "sum"), at_risk=("risk", "sum"),
                                                         old_units=("old", "sum"))
    day = f"{pl.run:%Y-%m-%d}"
    db.delete_rows("kpi_history", {"plan_id": plan_id})
    db.insert_rows("kpi_history", [dict(plan_id=plan_id, plan_date=day, store_id=r.store_id, category=str(r.dcat), on_hand=int(r.on_hand),
                                        pred7=float(r.pred7), selling=int(r.selling), in_stock=int(r.in_stock), at_risk=int(r.at_risk),
                                        old_units=int(r.old_units), at=datetime.now()) for r in rows.itertuples()])


def history():
    import db
    try:
        return db.read("kpi_history")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def history_vs_actual(h, sales):
    """Each saved plan's 7-day forecast next to what actually sold in those 7 days (where the data now covers them)."""
    if h is None or h.empty:
        return pd.DataFrame()
    s = sales.assign(q=sales.qty.clip(lower=0))
    last = s.date.max()
    out = []
    for (pid, day), grp in h.groupby(["plan_id", "plan_date"]):
        start = pd.Timestamp(day)
        if start + pd.Timedelta(days=6) > last:
            out.append(dict(plan_date=day, predicted=grp.pred7.sum(), actual=np.nan, complete=False))
            continue
        act = s[(s.date >= start) & (s.date < start + pd.Timedelta(days=7)) & s.store_id.isin(grp.store_id.unique())].q.sum()
        out.append(dict(plan_date=day, predicted=grp.pred7.sum(), actual=float(act), complete=True))
    return pd.DataFrame(out).sort_values("plan_date")
