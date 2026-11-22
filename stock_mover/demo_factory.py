"""Personalised demo data for a prospect: a fictional copy of their business (their category, number of warehouses and
stores, their cities) with realistic problems planted, so the first demo shows their own kind of day.

    import demo_factory; data = demo_factory.make("Sharma Toys", "toys", warehouses=2, stores=5, cities=["Kolkata", "Patna"])
    open("demo.zip", "wb").write(data)                 # upload it in StockFlow like any export

Files are in POS/ERP-export style (Indian dates, ₹ amounts, mixed headers) so the standardising step is shown too.
Problems planted: one flagship running out of its best category, one slow store sitting on expensive stock, a category
nearly out chain-wide (a buying signal), some old stock, and (grocery / pharmacy) batches close to expiry.
The data is invented. Company names are only used as labels.
"""
import io
import json
import re
import zipfile

import numpy as np
import pandas as pd

# category presets: {category: (number of items, (price low, high), share of demand, case pack, supplier, [names])}
PRESETS = {
    "toys": dict(label="Toys and games", profile="general_retail", age=120, cover=21, expiry=False, cats={
        "Soft Toys": (12, (299, 1999), .18, 12, "Cuddle Craft", ["Teddy Bear", "Unicorn Plush", "Panda Plush", "Bunny Plush"]),
        "Action Figures": (10, (399, 2499), .14, 12, "HeroWorks", ["Hero Figure", "Robot Warrior", "Space Ranger"]),
        "Board Games": (12, (199, 1499), .18, 12, "Brainy Box", ["Ludo", "Chess Set", "Puzzle 500pc", "Word Game"]),
        "STEM Kits": (10, (599, 3499), .12, 6, "Little Einstein", ["Robot Kit", "Science Lab", "Solar Car Kit"]),
        "Remote Control": (6, (999, 5999), .08, 4, "Zoom RC", ["RC Car", "RC Truck", "RC Helicopter"]),
        "Baby & Toddler": (10, (199, 1799), .30, 12, "Tiny Steps", ["Stacking Rings", "Rattle Set", "Shape Sorter"])}),
    "apparel": dict(label="Clothing and footwear", profile="apparel", age=90, cover=28, expiry=False, cats={
        "Kurtas": (14, (799, 2999), .26, 10, "Weave House", ["Cotton Kurta", "Printed Kurta", "Silk Kurta"]),
        "Sarees": (10, (1499, 8999), .14, 5, "Banaras Looms", ["Cotton Saree", "Silk Saree", "Georgette Saree"]),
        "Shirts": (12, (699, 2499), .22, 10, "Urban Thread", ["Formal Shirt", "Casual Shirt", "Linen Shirt"]),
        "Jeans": (8, (999, 2999), .18, 10, "Denim Works", ["Slim Jeans", "Straight Jeans"]),
        "Footwear": (8, (899, 3999), .20, 6, "StepRight", ["Sandals", "Sneakers", "Juttis"])}),
    "electronics": dict(label="Electronics and appliances", profile="electronics", age=150, cover=21, expiry=False, cats={
        "Mobiles": (10, (7999, 39999), .30, 5, "Phonetech", ["Phone 4G", "Phone 5G", "Phone Pro"]),
        "Accessories": (14, (199, 2999), .35, 20, "GadgetHub", ["Charger", "Earbuds", "Power Bank", "Screen Guard"]),
        "Televisions": (6, (13999, 59999), .10, 1, "Vision Displays", ["32in LED TV", "43in Smart TV", "55in 4K TV"]),
        "Kitchen Appliances": (10, (999, 8999), .25, 4, "HomeChef", ["Mixer Grinder", "Kettle", "Induction Cooktop"])}),
    "grocery": dict(label="Grocery and FMCG", profile="grocery_fmcg", age=60, cover=10, expiry=True, cats={
        "Staples": (14, (49, 699), .35, 24, "Annapurna Foods", ["Atta 5kg", "Rice 5kg", "Toor Dal 1kg", "Sugar 1kg"]),
        "Oils & Ghee": (8, (129, 899), .15, 12, "Golden Press", ["Mustard Oil 1L", "Sunflower Oil 1L", "Ghee 500ml"]),
        "Snacks": (14, (10, 199), .25, 48, "Crunchy Co", ["Namkeen", "Biscuits", "Chips"]),
        "Personal Care": (12, (49, 499), .25, 24, "FreshCare", ["Soap", "Shampoo", "Toothpaste"])}),
    "pharmacy": dict(label="Pharmacy and health", profile="pharmacy", age=120, cover=21, expiry=True, cats={
        "OTC Medicines": (16, (19, 299), .40, 50, "Medline Distributors", ["Paracetamol 500", "Cough Syrup", "Antacid"]),
        "Wellness": (12, (149, 1299), .25, 12, "Vital Health", ["Multivitamin", "Protein Powder", "Omega 3"]),
        "Personal Care": (12, (59, 599), .20, 24, "SkinFirst", ["Sunscreen", "Moisturiser", "Hand Wash"]),
        "Devices": (6, (499, 2999), .15, 4, "CareTech", ["BP Monitor", "Thermometer", "Oximeter"])}),
    "spare_parts": dict(label="Spare parts and hardware", profile="spare_parts", age=240, cover=30, expiry=False, cats={
        "Engine Parts": (14, (199, 4999), .30, 10, "AutoCore", ["Oil Filter", "Spark Plug", "Piston Ring"]),
        "Brakes": (10, (299, 2999), .25, 10, "StopSafe", ["Brake Pad", "Brake Shoe", "Disc Rotor"]),
        "Electricals": (12, (99, 2499), .25, 20, "VoltLine", ["Headlamp", "Battery", "Fuse Kit"]),
        "Tools": (8, (199, 3999), .20, 6, "ToolMaster", ["Spanner Set", "Jack", "Torque Wrench"])}),
    "general": dict(label="General retail", profile="general_retail", age=120, cover=21, expiry=False, cats={
        "Home": (12, (199, 2999), .25, 12, "HomeStyle", ["Bedsheet", "Cushion", "Storage Box"]),
        "Kitchen": (12, (149, 2499), .25, 12, "CookRight", ["Pressure Cooker", "Tawa", "Lunch Box"]),
        "Stationery": (12, (10, 499), .25, 50, "WriteWell", ["Notebook", "Pen Pack", "Art Set"]),
        "Bags": (8, (499, 2999), .25, 6, "CarryOn", ["Backpack", "Trolley Bag", "Tote"])}),
}
DEFAULT_CITIES = ["Kolkata", "Patna", "Ranchi", "Bhubaneswar", "Guwahati", "Siliguri", "Durgapur", "Cuttack"]


def category_of(text):
    t = str(text or "").lower()
    for k, words in (("toys", "toy game"), ("apparel", "apparel cloth fashion garment ethnic saree kurta footwear shoe"),
                     ("electronics", "electronic mobile phone appliance gadget"), ("grocery", "grocery fmcg supermarket food kirana"),
                     ("pharmacy", "pharma chemist medic health"), ("spare_parts", "spare part auto hardware tool")):
        if any(w in t for w in words.split()):
            return k
    return "general"


def _code(s, n=3):
    return re.sub(r"[^A-Z]", "", str(s).upper())[:n] or "X"


def make(company, category="general", warehouses=2, stores=5, cities=None, days=210, seed=None, today=None):
    """Returns the bytes of a zip ready to upload (CSV exports + config.json)."""
    P = PRESETS.get(category) or PRESETS[category_of(category)]
    warehouses, stores = max(1, min(int(warehouses), 8)), max(2, min(int(stores), 60))
    rng = np.random.default_rng(seed if seed is not None else abs(hash((company, category, warehouses, stores))) % 2**32)
    today = pd.Timestamp(today or pd.Timestamp.now().normalize() - pd.Timedelta(days=1))
    dts = pd.date_range(today - pd.Timedelta(days=days - 1), today)
    cities = [c.strip().title() for c in (cities or []) if str(c).strip()] or DEFAULT_CITIES
    zone = "East"
    wh = pd.DataFrame([(f"WH-{_code(cities[i % len(cities)])}{'' if i < len(cities) else i}", cities[i % len(cities)], zone)
                       for i in range(warehouses)], columns=["warehouse_id", "city", "zone"])
    grades = ["A"] + ["B"] * max(1, stores // 3) + ["C"] * stores
    seen, st_rows = {}, []
    for i in range(stores):
        city = cities[i % len(cities)]
        seen[city] = seen.get(city, 0) + 1
        st_rows.append((f"{_code(city)}-{seen[city]:02d}", city, city, zone, grades[i], int(rng.integers(15, 45)) * 100,
                        f"{city} store {seen[city]}"))
    st = pd.DataFrame(st_rows, columns=["Store Code", "City", "State", "Zone", "Grade", "Capacity", "Store Name"])
    items, n = [], 0
    for cat, (cnt, (lo, hi), share, carton, vendor, nm) in P["cats"].items():
        for j in range(1, cnt + 1):
            n += 1
            mrp = int(rng.integers(lo, hi + 1) // 10 * 10 + 9)
            items.append({"Item Code": f"{_code(cat)}-{j:03d}", "Barcode": f"890{7000000 + n * 7:09d}",
                          "Item Name": f"{nm[(j - 1) % len(nm)]}" + (f" {chr(65 + (j - 1) // len(nm))}" if j > len(nm) else ""),
                          "Category": cat, "MRP": mrp, "Landed Cost": int(mrp * rng.uniform(.5, .68)), "Supplier": vendor,
                          "Case Pack": carton, "Min Order Qty": carton * int(rng.choice([1, 2])), "_w": share * rng.lognormal(0, .7) / cnt})
    it = pd.DataFrame(items)
    W = it._w.values / it._w.sum()
    K = len(it)
    lift = np.where(dts.dayofweek == 6, 1.4, 1.0) * (1 + .15 * np.sin(np.arange(len(dts)) / 29))
    strength = {"A": 60.0, "B": 36.0, "C": 20.0}
    cats = list(P["cats"])
    best_cat, slow_cat, short_cat = cats[0], max(cats, key=lambda c: P["cats"][c][1][1]), cats[min(1, len(cats) - 1)]
    sales, snap = [], []
    for k_, s in st.iterrows():
        base = strength[s.Grade] * rng.uniform(.85, 1.15)
        carried = rng.random(K) < (.97 if s.Grade == "A" else .85)
        q = rng.poisson(np.outer(lift * base, W)) * carried
        d, kk = np.nonzero(q)
        sales.append(pd.DataFrame({"Bill Date": dts[d].strftime("%d/%m/%Y"), "Store Code": s["Store Code"], "Item Code": it["Item Code"].values[kk],
                                   "Qty": q[d, kk], "Net Amount": (it.MRP.values[kk] * q[d, kk] * rng.choice([1, 1, .9], len(d))).round(2)}))
        oh = np.where(carried, rng.poisson(base * W * rng.uniform(5, 40, K)), 0)
        if k_ == 0:                                           # flagship running out of its best category
            oh = np.where(it.Category.values == best_cat, rng.integers(0, 2, K), oh)
        if k_ == len(st) - 1:                                 # slow store sitting on the expensive category
            oh = oh + (it.Category.values == slow_cat) * rng.integers(6, 15, K)
        age = rng.choice([10, 30, 60, 100, 160, 260], K, p=[.25, .25, .2, .15, .1, .05])
        kk = np.nonzero(oh)[0]
        row = {"Stock Date": today.strftime("%d-%m-%Y"), "Site": s["Store Code"], "Item Code": it["Item Code"].values[kk],
               "Closing Stock": oh[kk], "In Transit": 0,
               "First GRN Date": [(today - pd.Timedelta(days=int(a))).strftime("%d-%m-%Y") for a in age[kk]]}
        if P["expiry"]:
            row["Expiry Date"] = [(today + pd.Timedelta(days=int(x))).strftime("%d-%m-%Y") for x in rng.choice([20, 45, 120, 300, 500], len(kk))]
        snap.append(pd.DataFrame(row))
    chain = sum(strength[g] for g in st.Grade) * W
    for i, w in enumerate(wh.warehouse_id):
        oh = rng.poisson(chain / len(wh) * rng.uniform(10, 35, K))
        oh = np.where(it.Category.values == short_cat, oh // 8, oh)    # nearly out chain-wide: a buying signal
        kk = np.nonzero(oh)[0]
        row = {"Stock Date": today.strftime("%d-%m-%Y"), "Site": w, "Item Code": it["Item Code"].values[kk], "Closing Stock": oh[kk],
               "In Transit": 0, "First GRN Date": (today - pd.Timedelta(days=40)).strftime("%d-%m-%Y")}
        if P["expiry"]:
            row["Expiry Date"] = (today + pd.Timedelta(days=200)).strftime("%d-%m-%Y")
        snap.append(pd.DataFrame(row))
    lanes = pd.DataFrame([(w, f"city:{c}", 1, 8, 600) for w, c in zip(wh.warehouse_id, wh.city)] +
                         [(w, "*", 3, 18, 2200) for w in wh.warehouse_id] + [("*", "*", 3, 22, 2800)],
                         columns=["from", "to", "lead_days", "cost_per_unit", "cost_per_trip"])
    cal = pd.DataFrame([(d.date(), r, "", int(d.dayofweek == 6)) for r in st.State.unique()
                        for d in pd.date_range(dts[0], today + pd.Timedelta(days=60))], columns=["date", "region", "event", "is_peak_day"])
    cfg = {"brand": {"name": f"{company} (demo)", "currency": "₹", "number_style": "indian"},
           "planning": {"cover_days": P["cover"], "donor_min_cover": P["cover"], "tier_order": ["A", "B", "C"]},
           "ageing": {"age_days": P["age"]}}
    files = {"store_master.csv": st, "warehouses.csv": wh, "item_master.csv": it.drop(columns="_w"),
             "pos_sales_export.csv": pd.concat(sales), "closing_stock.csv": pd.concat(snap), "lanes.csv": lanes, "calendar.csv": cal}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in files.items():
            z.writestr(name, df.to_csv(index=False))
        z.writestr("config.json", json.dumps(cfg, indent=1, ensure_ascii=False))
        z.writestr("README.txt", f"Demo data for {company}: {P['label']}, {len(wh)} warehouses, {len(st)} stores, {K} items, "
                                 f"{days} days of sales to {today:%d %b %Y}. Invented data, for a StockFlow demo only.\n"
                                 f"Planted: {st['Store Code'].iloc[0]} is short of {best_cat}; {st['Store Code'].iloc[-1]} holds too much "
                                 f"{slow_cat}; {short_cat} is nearly out chain-wide.\n")
    return buf.getvalue()


def story(company, category, warehouses, stores, cities=None):
    """What the demo will show, in words (for the pitch and the demo script)."""
    P = PRESETS.get(category) or PRESETS[category_of(category)]
    cats = list(P["cats"])
    return (f"In the demo, {company}'s flagship is short of {cats[0]}, the smallest store is holding too much "
            f"{max(cats, key=lambda c: P['cats'][c][1][1])}, and {cats[min(1, len(cats) - 1)]} is nearly out across the "
            f"chain. StockFlow plans the transfers, flags what to buy, and shows in-stock before and after.")
