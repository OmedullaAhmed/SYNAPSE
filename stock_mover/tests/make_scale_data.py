"""Synthetic data pack for a brand with N stores (for scale tests and evaluation screenshots).

python tests/make_scale_data.py 50 data/scale_50 [--days 182] [--styles 80] [--seed 1]
Fictional cities and codes; no real business data.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("stores", type=int)
ap.add_argument("out")
ap.add_argument("--days", type=int, default=182)
ap.add_argument("--styles", type=int, default=80)
ap.add_argument("--seed", type=int, default=1)
a = ap.parse_args()
rng = np.random.default_rng(a.seed)
OUT = Path(a.out)
OUT.mkdir(parents=True, exist_ok=True)
TODAY = pd.Timestamp("2026-10-31")
days = pd.date_range(TODAY - pd.Timedelta(days=a.days - 1), TODAY)

zones = {"North": ["Punjab", "Delhi NCR", "Rajasthan"], "Central": ["Madhya Pradesh", "Chhattisgarh"],
         "West": ["Maharashtra", "Gujarat"], "East": ["West Bengal", "Bihar", "Odisha"], "South": ["Karnataka", "Tamil Nadu", "Telangana"]}
regions = [(z, r) for z, rs in zones.items() for r in rs]
n_wh = max(1, min(len(zones), a.stores // 25 + 1))
wh_zones = list(zones)[:n_wh]
rows = []
for i in range(a.stores):
    z, r = regions[i % len(regions)]
    tier = rng.choice(["A", "B", "C"], p=[.25, .45, .3])
    cap = {"A": 2400, "B": 1500, "C": 900}[tier]
    city = f"{r.split()[0]} City {i // len(regions) + 1}"
    rows.append((f"S{i + 1:04d}", city, r, z, tier, cap, (TODAY - pd.Timedelta(days=int(rng.integers(90, 3000)))).date(),
                 "outlet" if i % 40 == 39 else "store", "", f"AM-{i // 8 + 1:03d}"))
stores = pd.DataFrame(rows, columns=["store_id", "city", "region", "zone", "tier", "capacity_units", "opened_on", "store_type",
                                     "home_warehouse", "manager_id"])
stores.loc[stores.store_type == "outlet", "tier"] = "Outlet"
warehouses = pd.DataFrame([(f"WH-{z[:3].upper()}", f"{zones[z][0].split()[0]} Hub", z) for z in wh_zones], columns=["warehouse_id", "city", "zone"])

cats = {"Kurta Set": (["S", "M", "L", "XL", "XXL"], (1999, 6999), .38), "Sherwani": (["38", "40", "42", "44", "46"], (12999, 39999), .10),
        "Nehru Jacket": (["S", "M", "L", "XL", "XXL"], (2499, 6999), .17), "Lehenga": (["S", "M", "L", "XL"], (8999, 29999), .15),
        "Saree": (["FS"], (2999, 14999), .10), "Juttis": (["6", "7", "8", "9", "10"], (999, 2999), .10)}
sw = {5: np.array([.12, .26, .30, .20, .12]), 4: np.array([.2, .35, .3, .15]), 1: np.array([1.0])}
colours = ["Maroon", "Ivory", "Teal", "Mustard", "Navy", "Peach", "Emerald", "Rose", "Gold", "Wine"]
items = []
for cat, (sizes, (lo, hi), share) in cats.items():
    n = max(2, int(round(a.styles * share)))
    for j in range(1, n + 1):
        sid = f"{cat[:3].upper()}-{j:03d}"
        mrp = int(rng.integers(lo, hi) // 100 * 100 + 99)
        pop = rng.lognormal(0, .7)
        end = (TODAY - pd.Timedelta(days=15)).date() if j % 17 == 0 else ""
        for c in rng.choice(colours, int(rng.choice([1, 2], p=[.5, .5])), replace=False):
            for sz, w in zip(sizes, sw[len(sizes)]):
                items.append(dict(sku_id=f"{sid}-{c[:3].upper()}-{sz}", style_id=sid, style_name=f"{c} {cat} {j}", category=cat, size=sz,
                                  colour=c, fit="Regular", mrp=mrp, cost_price=int(mrp * rng.uniform(.32, .46)),
                                  launch_date=(TODAY - pd.Timedelta(days=int(rng.choice([45, 200, 400, 700])))).date(),
                                  season_end=end, is_core_style=int(j <= n // 2), is_non_product=0, _w=share * pop * w / n))
items = pd.DataFrame(items)
W = items._w.values / items._w.sum()
K = len(items)

# calendar: festival weeks + wedding-like peak days
cal_days = pd.date_range(days[0], TODAY + pd.Timedelta(days=120))
fest = {pd.Timestamp("2026-03-04"): "Holi", pd.Timestamp("2026-10-20"): "Dussehra", pd.Timestamp("2026-11-08"): "Diwali"}
cal = []
for _, r in regions:
    peak = (np.isin(cal_days.month, [11, 12, 1, 2]) & (rng.random(len(cal_days)) < .22)) | \
           (np.isin(cal_days.month, [4, 5]) & (rng.random(len(cal_days)) < .1))
    cal += [(d.date(), r, fest.get(d, ""), int(p)) for d, p in zip(cal_days, peak)]
calendar = pd.DataFrame(cal, columns=["date", "region", "event", "is_peak_day"])
lift = {}
for r, g in calendar.groupby("region"):
    gi = g.set_index(pd.to_datetime(g.date))
    ev = gi.event.ne("").reindex(cal_days, fill_value=False)
    pre = ev[::-1].rolling(7, min_periods=1).max()[::-1].astype(bool)
    lift[r] = (1 + 1.4 * gi.is_peak_day.reindex(days).values + .8 * pre.reindex(days).values)

weeks = pd.date_range(end=TODAY, periods=12, freq="7D")
sales, weekly, snap = [], [], []
strength = {"A": 34.0, "B": 20.0, "C": 11.0, "Outlet": 8.0}
for _, st in stores.iterrows():
    base = strength[st.tier] * rng.uniform(.8, 1.2)
    # in-stock flags per week (random walk), expanded to days
    flag = np.ones((len(weeks) + 30, K), bool)
    flag[0] = rng.random(K) < .88
    for t in range(1, flag.shape[0]):
        flip = rng.random(K) < .07
        flag[t] = np.where(flip, ~flag[t - 1], flag[t - 1])
    day_flag = np.repeat(flag, 7, axis=0)[-len(days):]
    lam = np.outer(lift[st.region] * base, W)
    q = rng.poisson(lam) * day_flag
    d_idx, k_idx = np.nonzero(q)
    disc = rng.choice([1, 1, 1, .9, .8], len(d_idx))
    sales.append(pd.DataFrame({"date": days[d_idx].date, "store_id": st.store_id, "sku_id": items.sku_id.values[k_idx],
                               "qty": q[d_idx, k_idx], "net_value": (items.mrp.values[k_idx] * q[d_idx, k_idx] * disc).round()}))
    wf = flag[-len(weeks):]
    dis = np.where(wf, 7, rng.choice([0, 0, 1, 2], wf.shape))
    weekly.append(pd.DataFrame({"week_ending": np.repeat(weeks.date, K), "store_id": st.store_id,
                                "sku_id": np.tile(items.sku_id.values, len(weeks)), "days_in_stock": dis.ravel()}))
    rate = base * W
    oh = np.where(flag[-1], rng.poisson(rate * rng.uniform(12, 55, K)), 0)
    over = (oh > 0) & (rng.random(K) < .12)
    oh = oh + over * rng.integers(2, 6, K)
    if st.store_type == "outlet":
        oh = rng.poisson(1.2, K)
    age = rng.choice([10, 30, 60, 90, 150, 220, 400], K, p=[.2, .2, .2, .15, .12, .08, .05])
    k = np.nonzero(oh)[0]
    snap.append(pd.DataFrame({"as_of_date": TODAY.date(), "location_id": st.store_id, "sku_id": items.sku_id.values[k], "on_hand": oh[k],
                              "in_transit": 0, "oldest_stock_received_on": [(TODAY - pd.Timedelta(days=int(x))).date() for x in age[k]],
                              "stock_status": "sellable"}))
    bad = rng.random(K) < .004
    if bad.any():
        kb = np.nonzero(bad)[0]
        snap.append(pd.DataFrame({"as_of_date": TODAY.date(), "location_id": st.store_id, "sku_id": items.sku_id.values[kb], "on_hand": 1,
                                  "in_transit": 0, "oldest_stock_received_on": (TODAY - pd.Timedelta(days=40)).date(),
                                  "stock_status": rng.choice(["alteration", "damaged", "return_qc"], len(kb))}))
chain_rate = sum(strength[t] for t in stores.tier) * W
for w in warehouses.warehouse_id:
    oh = rng.poisson(chain_rate / n_wh * rng.uniform(10, 40, K))
    k = np.nonzero(oh)[0]
    snap.append(pd.DataFrame({"as_of_date": TODAY.date(), "location_id": w, "sku_id": items.sku_id.values[k], "on_hand": oh[k], "in_transit": 0,
                              "oldest_stock_received_on": (TODAY - pd.Timedelta(days=60)).date(), "stock_status": "sellable"}))
inbound = pd.DataFrame({"expected_date": [(TODAY + pd.Timedelta(days=int(x))).date() for x in rng.choice([1, 1, 5, 12], 60)],
                        "sku_id": rng.choice(items.sku_id, 60, replace=False), "qty": rng.integers(20, 120, 60),
                        "warehouse_id": rng.choice(warehouses.warehouse_id, 60)})
lanes = [(w, f"zone:{z}", 2 if z == wz else 4, 30 if z == wz else 60, 800 if z == wz else 1800)
         for w, wz in zip(warehouses.warehouse_id, warehouses.zone) for z in zones]
lanes += [(f"zone:{z}", f"zone:{z}", 3, 40, 1000) for z in zones] + [("*", "*", 6, 110, 3000)]
lanes = pd.DataFrame(lanes, columns=["from", "to", "lead_days", "cost_per_unit", "cost_per_trip"])
md = pd.DataFrame([(t, c, int(n * f)) for t, f in (("A", 1.0), ("B", .6), ("C", .35)) for c, n in
                   (("Kurta Set", 80), ("Sherwani", 15), ("Nehru Jacket", 30), ("Lehenga", 20), ("Saree", 15), ("Juttis", 15))],
                  columns=["tier", "category", "min_display_units"])
prot = stores[stores.tier == "A"].head(5).assign(style_id="KUR-001")[["store_id", "style_id"]]
mgr = stores.groupby("manager_id").store_id.apply(lambda s: ", ".join(s)).reset_index(name="stores")
mgr["name"] = [f"Manager {i + 1}" for i in range(len(mgr))]
mgr["max_alerts_per_week"] = 5
for name, df in dict(stores=stores, warehouses=warehouses, items=items.drop(columns="_w"), sales=pd.concat(sales), stock_snapshot=pd.concat(snap),
                     stock_weekly=pd.concat(weekly), inbound=inbound, lanes=lanes, calendar=calendar, min_display=md,
                     protected_styles=prot, managers=mgr[["manager_id", "name", "stores", "max_alerts_per_week"]]).items():
    df.to_csv(OUT / f"{name}.csv", index=False)
print(f"{a.stores} stores, {len(warehouses)} warehouses, {K} SKUs, {sum(len(s) for s in sales):,} sales lines -> {OUT}")
