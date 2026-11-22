"""A fictional general retailer for the second demo: 'Demo Mart', 10 stores and 2 warehouses selling electronics, home and
kitchen goods, mobile accessories and groceries (with expiry dates). No sizes, no colours, no fashion seasons.

    python tests/make_general_demo.py data/demo_general
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo_general")
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(7)
TODAY = pd.Timestamp("2026-10-31")
days = pd.date_range(TODAY - pd.Timedelta(days=181), TODAY)

stores = pd.DataFrame([
    ("KOL-01", "Kolkata", "West Bengal", "East", "A", 6000), ("KOL-02", "Kolkata", "West Bengal", "East", "B", 3500),
    ("SIL-01", "Siliguri", "West Bengal", "East", "C", 2200), ("BBS-01", "Bhubaneswar", "Odisha", "East", "B", 3200),
    ("PAT-01", "Patna", "Bihar", "East", "B", 3000), ("RAN-01", "Ranchi", "Jharkhand", "East", "C", 2200),
    ("DEL-01", "Delhi", "Delhi NCR", "North", "A", 6500), ("GGN-01", "Gurugram", "Delhi NCR", "North", "A", 5000),
    ("LKO-01", "Lucknow", "Uttar Pradesh", "North", "B", 3200), ("JAI-01", "Jaipur", "Rajasthan", "North", "C", 2400)],
    columns=["store_id", "city", "region", "zone", "tier", "capacity_units"])
stores["store_type"], stores["manager_id"] = "store", np.where(stores.zone == "East", "RM-EAST", "RM-NORTH")
warehouses = pd.DataFrame([("DC-KOL", "Kolkata", "East"), ("DC-DEL", "Delhi", "North")], columns=["warehouse_id", "city", "zone"])

cats = {  # category: (count, price range, demand share, shelf life days or 0)
    "Mobile Accessories": (30, (299, 2499), .24, 0), "Small Appliances": (20, (999, 7999), .12, 0),
    "Televisions": (8, (14999, 64999), .03, 0), "Home & Kitchen": (30, (199, 2999), .18, 0),
    "Packaged Food": (30, (49, 699), .30, 120), "Personal Care": (20, (99, 899), .13, 365)}
names = {"Mobile Accessories": ["USB-C Cable", "Fast Charger", "Power Bank", "Earbuds", "Phone Case", "Screen Guard"],
         "Small Appliances": ["Mixer Grinder", "Electric Kettle", "Iron", "Induction Cooktop", "Air Fryer"],
         "Televisions": ['32" LED TV', '43" Smart TV', '55" 4K TV'], "Home & Kitchen": ["Steel Bottle", "Cookware Set", "Storage Jar", "Bedsheet", "Towel"],
         "Packaged Food": ["Basmati Rice 5kg", "Atta 10kg", "Green Tea", "Biscuits", "Cooking Oil 1L", "Masala Pack"],
         "Personal Care": ["Shampoo", "Face Wash", "Toothpaste", "Soap Pack", "Body Lotion"]}
items = []
for cat, (n, (lo, hi), share, life) in cats.items():
    for j in range(1, n + 1):
        base = names[cat][(j - 1) % len(names[cat])]
        mrp = int(rng.integers(lo, hi) // 10 * 10 + 9)
        items.append(dict(sku_id=f"{cat[:3].upper()}{j:03d}", product_name=f"{base} {chr(65 + (j - 1) // len(names[cat]))}",
                          category=cat, price=mrp, cost=int(mrp * rng.uniform(.55, .78)), _w=share * rng.lognormal(0, .8) / n, _life=life))
items = pd.DataFrame(items)
W = items._w.values / items._w.sum()
K = len(items)

cal_days = pd.date_range(days[0], TODAY + pd.Timedelta(days=90))
fest = {pd.Timestamp("2026-10-20"): "Dussehra", pd.Timestamp("2026-11-08"): "Diwali", pd.Timestamp("2026-08-15"): "Independence Day sale"}
calendar = pd.DataFrame([(d.date(), r, fest.get(d, ""), int(d.dayofweek >= 5)) for r in stores.region.unique() for d in cal_days],
                        columns=["date", "region", "event", "is_peak_day"])

strength = {"A": 160.0, "B": 95.0, "C": 55.0}
sales, snap = [], []
for _, st in stores.iterrows():
    base = strength[st.tier] * rng.uniform(.85, 1.15)
    wk = np.where(days.dayofweek >= 5, 1.35, 1.0) * np.where((days >= "2026-10-14") & (days <= "2026-10-20"), 1.6, 1.0)
    instock = rng.random(K) < .9
    q = rng.poisson(np.outer(wk * base, W)) * instock
    d_idx, k_idx = np.nonzero(q)
    sales.append(pd.DataFrame({"Bill Date": days[d_idx].strftime("%d/%m/%Y"), "Store Code": st.store_id, "Item Code": items.sku_id.values[k_idx],
                               "Qty": q[d_idx, k_idx], "Net Amount": (items.price.values[k_idx] * q[d_idx, k_idx] * rng.choice([1, 1, .95, .9], len(d_idx))).round()}))
    rate = base * W
    oh = np.where(instock, rng.poisson(rate * rng.uniform(8, 60, K)), 0)
    if st.store_id in ("SIL-01", "RAN-01"):            # slow stores sitting on too much
        oh = oh + rng.poisson(rate * 40)
    age = rng.choice([7, 20, 45, 90, 150, 260], K, p=[.25, .25, .2, .15, .1, .05])
    life = items._life.values
    exp = np.where(life > 0, [int(x) for x in rng.integers(5, 200, K)], 0)
    k = np.nonzero(oh)[0]
    snap.append(pd.DataFrame({"Stock Date": TODAY.strftime("%d-%m-%Y"), "Site": st.store_id, "Item Code": items.sku_id.values[k],
                              "Closing Stock": oh[k], "In Transit": 0,
                              "First GRN Date": [(TODAY - pd.Timedelta(days=int(x))).strftime("%d-%m-%Y") for x in age[k]],
                              "Expiry Date": [(TODAY + pd.Timedelta(days=int(e))).strftime("%d-%m-%Y") if l else "" for e, l in zip(exp[k], life[k])]}))
chain = sum(strength[t] for t in stores.tier) * W
for w in warehouses.warehouse_id:
    oh = rng.poisson(chain / 2 * rng.uniform(6, 30, K))
    k = np.nonzero(oh)[0]
    snap.append(pd.DataFrame({"Stock Date": TODAY.strftime("%d-%m-%Y"), "Site": w, "Item Code": items.sku_id.values[k], "Closing Stock": oh[k],
                              "In Transit": 0, "First GRN Date": (TODAY - pd.Timedelta(days=30)).strftime("%d-%m-%Y"),
                              "Expiry Date": [(TODAY + pd.Timedelta(days=150)).strftime("%d-%m-%Y") if items._life.values[i] else "" for i in k]}))
lanes = pd.DataFrame([("DC-KOL", "zone:East", 2, 12, 900), ("DC-DEL", "zone:North", 2, 12, 900), ("DC-KOL", "zone:North", 5, 30, 4000),
                      ("DC-DEL", "zone:East", 5, 30, 4000), ("zone:East", "zone:East", 2, 15, 1200), ("zone:North", "zone:North", 2, 15, 1200),
                      ("*", "*", 6, 40, 4500)], columns=["from", "to", "lead_days", "cost_per_unit", "cost_per_trip"])

stores.to_csv(OUT / "store_master.csv", index=False)
warehouses.to_csv(OUT / "warehouses.csv", index=False)
items.drop(columns=["_w", "_life"]).to_csv(OUT / "item_master.csv", index=False)
pd.concat(sales).to_csv(OUT / "pos_sales_export.csv", index=False)
pd.concat(snap).to_csv(OUT / "closing_stock.csv", index=False)
calendar.to_csv(OUT / "calendar.csv", index=False)
lanes.to_csv(OUT / "lanes.csv", index=False)
print(f"{len(stores)} stores, {len(warehouses)} warehouses, {K} SKUs, {sum(len(s) for s in sales):,} sales lines -> {OUT}")
