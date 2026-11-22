"""Dummy data for a toy wholesaler: 'Khel Khilona Distributors' (fictional), 2 warehouses, 5 stores, ~90 toys.

    python tests/make_toys_demo.py /tmp/toys

Writes CSVs in POS/ERP-export style (so the standardising step is exercised), a config.json, a printed stock-count
sheet image for the photo feature, and toys_demo.zip ready to upload.
"""
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/toys")
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(11)
TODAY = pd.Timestamp("2026-11-22")                      # yesterday's closing stock
days = pd.date_range(TODAY - pd.Timedelta(days=269), TODAY)

stores = pd.DataFrame([
    ("KOL-PG", "Kolkata", "West Bengal", "East", "A", 4200, "Park Street flagship"),
    ("KOL-SL", "Kolkata", "West Bengal", "East", "B", 2600, "Salt Lake"),
    ("HWH-01", "Howrah", "West Bengal", "East", "C", 1800, "Howrah Maidan"),
    ("PAT-01", "Patna", "Bihar", "East", "B", 2400, "Boring Road"),
    ("RAN-01", "Ranchi", "Jharkhand", "East", "C", 1600, "Main Road")],
    columns=["Store Code", "City", "State", "Zone", "Grade", "Capacity", "Store Name"])
warehouses = pd.DataFrame([("WH-KOL", "Kolkata (Dankuni)", "East"), ("WH-PAT", "Patna (Fatuha)", "East")],
                          columns=["warehouse_id", "city", "zone"])

cats = {  # category: (count, price range, demand share, carton, supplier)
    "Soft Toys": (14, (299, 1999), .17, 12, "Cuddle Craft, Delhi"),
    "Action Figures": (12, (399, 2499), .14, 12, "HeroWorks Imports"),
    "Dolls & Doll Houses": (10, (499, 3999), .10, 6, "Pari Toys, Mumbai"),
    "Board Games & Puzzles": (14, (199, 1499), .16, 12, "Brainy Box, Bengaluru"),
    "STEM & Educational": (12, (599, 3499), .12, 6, "Little Einstein Kits"),
    "Remote Control": (8, (999, 5999), .08, 4, "Zoom RC Traders"),
    "Outdoor & Ride-ons": (8, (799, 6999), .06, 2, "Playfield Industries"),
    "Baby & Toddler": (12, (199, 1799), .17, 12, "Tiny Steps, Ludhiana")}
names = {
    "Soft Toys": ["Teddy Bear 30cm", "Teddy Bear 60cm", "Unicorn Plush", "Panda Plush", "Elephant Plush", "Bunny Plush", "Dino Plush"],
    "Action Figures": ["Super Hero Figure", "Robot Warrior", "Space Ranger", "Dino Hunter Set", "Ninja Figure", "Monster Truck Figure"],
    "Dolls & Doll Houses": ["Fashion Doll", "Baby Doll", "Doll House Small", "Doll House Deluxe", "Doll Kitchen Set"],
    "Board Games & Puzzles": ["Ludo Classic", "Snakes & Ladders", "Business Game", "Chess Set", "100pc Puzzle", "500pc Puzzle", "Word Game"],
    "STEM & Educational": ["Robot Building Kit", "Science Lab Kit", "Solar Car Kit", "Magnetic Tiles 60pc", "Coding Robot", "Globe Puzzle"],
    "Remote Control": ["RC Car 1:20", "RC Monster Truck", "RC Helicopter", "RC Stunt Car"],
    "Outdoor & Ride-ons": ["Kids Scooter", "Tricycle", "Badminton Set", "Cricket Set Junior"],
    "Baby & Toddler": ["Stacking Rings", "Rattle Set", "Shape Sorter", "Activity Cube", "Push Walker", "Bath Toys Set"]}
items = []
n = 0
for cat, (cnt, (lo, hi), share, carton, vendor) in cats.items():
    for j in range(1, cnt + 1):
        n += 1
        base = names[cat][(j - 1) % len(names[cat])]
        variant = (j - 1) // len(names[cat])
        body = f"890{6000 + n:09d}"
        chk = (10 - sum(int(d) * (3 if k % 2 else 1) for k, d in enumerate(body)) % 10) % 10
        mrp = int(rng.integers(lo, hi) // 10 * 10 + 9)
        items.append({"Item Code": f"TOY-{cat[:3].upper()}-{j:03d}", "Barcode": body + str(chk),
                      "Item Name": base + (f" ({['Blue', 'Pink', 'Green', 'Red'][variant % 4]})" if variant else ""),
                      "Category": cat, "MRP": mrp, "Landed Cost": int(mrp * rng.uniform(.48, .62)), "Supplier": vendor,
                      "Case Pack": carton, "Min Order Qty": carton * int(rng.choice([1, 2, 4])),
                      "_w": share * rng.lognormal(0, .75) / cnt,
                      "_battery": cat in ("Remote Control", "STEM & Educational")})
items = pd.DataFrame(items)
W = items._w.values / items._w.sum()
K = len(items)

# calendar: festival and gifting peaks
events = {"2026-03-04": "Holi", "2026-06-01": "Summer holidays", "2026-08-15": "Independence Day sale", "2026-10-20": "Durga Puja",
          "2026-11-08": "Diwali", "2026-11-14": "Children's Day", "2026-12-25": "Christmas", "2027-01-01": "New Year"}
cal_days = pd.date_range(days[0], TODAY + pd.Timedelta(days=60))
calendar = pd.DataFrame([(d.date(), r, events.get(d.strftime("%Y-%m-%d"), ""), int(d.dayofweek == 6))
                         for r in stores.State.unique() for d in cal_days], columns=["date", "region", "event", "is_peak_day"])
lift = np.ones(len(days))
for e in ("2026-10-20", "2026-11-08", "2026-11-14"):
    d = pd.Timestamp(e)
    lift += np.where((days > d - pd.Timedelta(days=10)) & (days <= d), 1.2, 0)
lift *= np.where(days.dayofweek == 6, 1.5, 1.0)
lift *= np.where((days.month >= 5) & (days.month <= 6), 1.3, 1.0)           # summer holidays

strength = {"A": 70.0, "B": 42.0, "C": 24.0}
sales, snap = [], []
for _, s in stores.iterrows():
    base = strength[s.Grade] * rng.uniform(.9, 1.1)
    carried = rng.random(K) < (.97 if s.Grade == "A" else .85)
    q = rng.poisson(np.outer(lift * base, W)) * carried
    d_idx, k_idx = np.nonzero(q)
    disc = rng.choice([1, 1, 1, .9, .85], len(d_idx))
    ret = rng.random(len(d_idx)) < .015
    sales.append(pd.DataFrame({"Bill Date": days[d_idx].strftime("%d/%m/%Y"), "Store Code": s["Store Code"],
                               "Item Code": items["Item Code"].values[k_idx], "Qty": q[d_idx, k_idx],
                               "Net Amount": (items.MRP.values[k_idx] * q[d_idx, k_idx] * disc).round(2),
                               "Txn Type": np.where(ret, "Return", "Sale"), "Customer Mobile": "98XXXXXX00"}))
    rate = base * W
    oh = np.where(carried, rng.poisson(rate * rng.uniform(5, 45, K)), 0)
    if s["Store Code"] == "RAN-01":                        # slow store sitting on RC cars and ride-ons
        heavy = items.Category.isin(["Remote Control", "Outdoor & Ride-ons"]).values
        oh = oh + heavy * rng.integers(8, 20, K)
    if s["Store Code"] == "KOL-PG":                        # flagship ran short of soft toys after Children's Day
        oh = np.where(items.Category.values == "Soft Toys", rng.integers(0, 2, K), oh)
    age = rng.choice([10, 30, 60, 100, 160, 240], K, p=[.25, .25, .2, .15, .1, .05])
    k = np.nonzero(oh)[0]
    snap.append(pd.DataFrame({"Stock Date": TODAY.strftime("%d-%m-%Y"), "Location": s["Store Code"], "Item Code": items["Item Code"].values[k],
                              "Closing Stock": oh[k], "In Transit": 0,
                              "GRN Date": [(TODAY - pd.Timedelta(days=int(a))).strftime("%d-%m-%Y") for a in age[k]],
                              "Status": "Available"}))
    dmg = rng.random(K) < .01
    if dmg.any():
        kd = np.nonzero(dmg)[0]
        snap.append(pd.DataFrame({"Stock Date": TODAY.strftime("%d-%m-%Y"), "Location": s["Store Code"], "Item Code": items["Item Code"].values[kd],
                                  "Closing Stock": 1, "In Transit": 0, "GRN Date": (TODAY - pd.Timedelta(days=50)).strftime("%d-%m-%Y"),
                                  "Status": "Damaged"}))
chain = sum(strength[g] for g in stores.Grade) * W
for w, share in (("WH-KOL", .65), ("WH-PAT", .35)):
    oh = rng.poisson(chain * share * rng.uniform(10, 40, K))
    oh = np.where(items.Category.values == "Soft Toys", oh // 6, oh)           # soft toys nearly out chain-wide: a buying signal
    k = np.nonzero(oh)[0]
    snap.append(pd.DataFrame({"Stock Date": TODAY.strftime("%d-%m-%Y"), "Location": w, "Item Code": items["Item Code"].values[k],
                              "Closing Stock": oh[k], "In Transit": 0, "GRN Date": (TODAY - pd.Timedelta(days=35)).strftime("%d-%m-%Y"),
                              "Status": "Available"}))
pick = rng.choice(K, 18, replace=False)
inbound = pd.DataFrame({"PO Delivery Date": [(TODAY + pd.Timedelta(days=int(x))).strftime("%d/%m/%Y") for x in rng.choice([1, 3, 9, 20], 18)],
                        "Item Code": items["Item Code"].values[pick], "Qty": items["Case Pack"].values[pick] * rng.integers(2, 10, 18),
                        "Warehouse": rng.choice(warehouses.warehouse_id, 18)})
lanes = pd.DataFrame([("WH-KOL", "city:Kolkata", 1, 8, 600), ("WH-KOL", "city:Howrah", 1, 8, 600), ("WH-KOL", "*", 3, 18, 2200),
                      ("WH-PAT", "city:Patna", 1, 8, 600), ("WH-PAT", "*", 3, 20, 2500), ("city:Kolkata", "city:Kolkata", 1, 10, 500),
                      ("city:Kolkata", "city:Howrah", 1, 10, 500), ("city:Howrah", "city:Kolkata", 1, 10, 500), ("*", "*", 4, 25, 3000)],
                     columns=["from", "to", "lead_days", "cost_per_unit", "cost_per_trip"])
managers = pd.DataFrame([("AM-KOL", "Area Manager Kolkata", "KOL-PG, KOL-SL, HWH-01", 5), ("AM-BJ", "Area Manager Bihar-Jharkhand", "PAT-01, RAN-01", 5)],
                        columns=["manager_id", "name", "stores", "max_alerts_per_week"])
stores["manager_id"] = ["AM-KOL", "AM-KOL", "AM-KOL", "AM-BJ", "AM-BJ"]

files = {
    "store_master.csv": stores, "warehouses.csv": warehouses, "item_master.csv": items.drop(columns=["_w", "_battery"]),
    "pos_sales_export.csv": pd.concat(sales), "closing_stock.csv": pd.concat(snap), "purchase_orders_due.csv": inbound,
    "calendar.csv": calendar, "lanes.csv": lanes, "managers.csv": managers}
for name, df in files.items():
    df.to_csv(OUT / name, index=False)
cfg = {"brand": {"name": "Khel Khilona Distributors (demo)", "currency": "₹", "number_style": "indian", "primary_color": "#C2410C"},
       "planning": {"cover_days": 21, "donor_min_cover": 21, "tier_order": ["A", "B", "C"]},
       "ageing": {"age_days": 120, "sell_within": 45, "pullback_cover": 150},
       "season": {"peak_label": "Sunday", "event_label": "festival week"},
       "economics": {"handling_per_unit": 6, "markdown_per_month": 0.02, "min_trip_units": 6}}
(OUT / "config.json").write_text(json.dumps(cfg, indent=1, ensure_ascii=False))

# a printed count sheet for the photo feature (Salt Lake store, 15 lines)
try:
    from PIL import Image, ImageDraw, ImageFont
    f = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    F = ImageFont.truetype(f, 26) if Path(f).exists() else ImageFont.load_default()
    FB = ImageFont.truetype(f.replace("Sans.ttf", "Sans-Bold.ttf"), 30) if Path(f).exists() else F
    sample = items.sample(15, random_state=5)
    im = Image.new("RGB", (1700, 1150), "white")
    dr = ImageDraw.Draw(im)
    dr.text((60, 40), "STOCK COUNT SHEET   Store: KOL-SL   Date: 22/11/2026", font=FB, fill="black")
    dr.text((60, 100), "Item Code          Description                         Qty", font=F, fill="black")
    y = 150
    for r in sample.itertuples():
        dr.text((60, y), f"{r._1:<16} {r._3[:30]}", font=F, fill="black")
        dr.text((1180, y), str(int(rng.integers(0, 30))), font=F, fill="black")
        dr.line((60, y + 42, 1300, y + 42), fill="#999")
        y += 60
    im.save(OUT / "count_sheet_KOL-SL.jpg", quality=88)
except Exception as e:  # noqa: BLE001
    print("count sheet image skipped:", e)

with zipfile.ZipFile(OUT / "toys_demo.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for name in list(files) + ["config.json"]:
        z.write(OUT / name, name)
print(f"{len(stores)} stores, {len(warehouses)} warehouses, {K} toys, {sum(len(s) for s in sales):,} sales lines -> {OUT}")
