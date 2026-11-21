"""Stock Mover: a daily ready-to-approve list of stock moves for Manyavar stores.

Run:  python stock_mover.py [--data DIR] [--out DIR]

Pipeline
  1. load + clean      packaging out, stock records reconciled, data issues logged
  2. velocity          sales per day *while in stock*, blended with a store x category prior
  3. season            calendar-driven uplift (wedding dates, run-up to festivals) by state
  4. plan              A warehouse -> store, B store -> store for shortages and min display,
                       C ageing stock -> stores where it sells, D pull back what nobody will sell
  5. validate          every rule re-checked independently on the final plan
  6. write             Excel approval workbook + CSVs
"""
import argparse
import heapq
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = next((p for p in (HERE / "data" / "csv", HERE.parent / "manyavar-vch3-data" / "csv") if p.exists()),
                    HERE / "data" / "csv")

CFG = dict(
    cover_days=28,          # selling days a delivery should cover after it lands
    donor_min_cover=30,     # rule: giving store keeps >= 30 days cover of the style
    shrink_days=28,         # in-stock days of evidence before a store's own velocity outweighs the prior
    week_decay=0.85,        # weight of each older week (half-life ~4 weeks)
    age_days=120,           # stock older than this is "ageing"
    age_cover=60,           # ...and only if the store holds more than this many days of it
    sell_within=45,         # ageing stock goes only where it will sell within this many days
    pullback_cover=120,     # aged stock still above this cover, with nowhere to sell it, goes back to WH
    approval_value=100_000, # rule: a single move above Rs 1,00,000 at MRP needs human approval
    inbound_usable_days=1,  # warehouse inbound due within this many days of the run can be allocated
)
ACC = "Accessories (Safa/Mala/Stole)"
TIER_RANK = {"S++": 0, "S+": 1, "A": 2, "B": 3}
WH = "WH-KOL"
T_WH, T_DISP, T_S2S, T_AGE, T_PULL = ("Warehouse -> store", "Min display top-up",
                                      "Store -> store", "Ageing stock", "Pull back to WH")


def days(x):
    return "over a year" if x > 365 else f"{x:.0f} days"


# ----------------------------------------------------------------------------- 1. load + clean
REQUIRED = {
    "stores": ["store_id", "city", "state", "zone", "tier", "stock_capacity_units", "opened_on", "area_manager_id"],
    "items": ["sku_id", "style_id", "style_name", "category", "size", "mrp", "launch_date", "is_core_style", "is_packaging"],
    "bills": ["bill_id", "bill_date", "store_id"],
    "bill_lines": ["bill_id", "sku_id", "qty"],
    "stock_snapshot": ["as_of_date", "location_id", "sku_id", "on_hand", "in_transit", "oldest_stock_received_on"],
    "stock_weekly": ["week_ending", "store_id", "sku_id", "closing_on_hand", "days_in_stock"],
    "warehouse_inbound": ["expected_date", "sku_id", "qty"],
    "calendar": ["date", "state", "festival", "is_wedding_date"],
    "min_display": ["tier", "category", "min_display_units"],
    "lead_times": ["route", "days"],
    "flagship_protected": ["store_id", "style_id"],
    "area_managers": ["area_manager_id", "name", "stores", "max_alerts_per_week"],
}
DATE_COLS = {"as_of_date", "oldest_stock_received_on", "in_transit_arrives_on", "week_ending", "expected_date",
             "date", "bill_date", "launch_date", "opened_on"}
FLAG_COLS = {"is_packaging", "is_core_style", "is_wedding_date"}
NUM_COLS = {"stock_snapshot": ["on_hand", "in_transit"], "stock_weekly": ["closing_on_hand", "days_in_stock"],
            "warehouse_inbound": ["qty"], "bill_lines": ["qty"], "min_display": ["min_display_units"]}


class DataError(Exception):
    """The uploaded data is missing something Stock Mover needs."""


def read_tables(source):
    """A folder of CSVs (searched recursively) or one Excel workbook with one tab per table."""
    source = Path(source)
    if source.is_file() and source.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        tabs = pd.read_excel(source, sheet_name=None, dtype=str, keep_default_na=False)
        return {k.strip().lower().replace(" ", "_"): v for k, v in tabs.items()}
    T = {}
    for f in sorted(source.rglob("*")):
        if f.suffix.lower() == ".csv":
            T.setdefault(f.stem.strip().lower().replace(" ", "_"), pd.read_csv(f, dtype=str, keep_default_na=False))
        elif f.suffix.lower() in (".xlsx", ".xlsm"):
            for k, v in read_tables(f).items():
                T.setdefault(k, v)
    return T


def check_tables(T):
    problems = []
    for t, cols in REQUIRED.items():
        if t not in T:
            problems.append(f"Missing table '{t}' (a file {t}.csv or an Excel tab named {t}).")
            continue
        T[t].columns = [str(c).strip().lower() for c in T[t].columns]
        miss = [c for c in cols if c not in T[t].columns]
        if miss:
            problems.append(f"Table '{t}' is missing column(s): {', '.join(miss)}.")
        elif len(T[t]) == 0:
            problems.append(f"Table '{t}' has no rows.")
    return problems


def load(source):
    T = read_tables(source)
    problems = check_tables(T)
    if problems:
        raise DataError("\n".join(problems))
    D = {t: T[t].copy() for t in REQUIRED}
    for df in D.values():
        for c in df.columns:
            df[c] = df[c].astype(str).str.strip().replace({"nan": "", "NaT": "", "None": ""})
            if c in DATE_COLS:   # Excel turns dates into '2026-10-31 00:00:00'
                d = pd.to_datetime(df[c], errors="coerce")
                df[c] = d.dt.strftime("%Y-%m-%d").fillna("")
            elif c in FLAG_COLS:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int).astype(str)
    D["stock_snapshot"]["in_transit"] = D["stock_snapshot"].in_transit.replace("", "0")
    for t, cols in NUM_COLS.items():
        for c in cols:
            v = pd.to_numeric(D[t][c], errors="coerce")
            if v.isna().any():
                raise DataError(f"Table '{t}', column '{c}' has {int(v.isna().sum())} values that are not numbers.")
            D[t][c] = v.astype(int)
    if "rank" not in D["flagship_protected"]:
        D["flagship_protected"]["rank"] = ""
    return D


def prep(D):
    ex = []  # data issues found

    def issue(kind, store, sku, detail, action, sev="High"):
        ex.append(dict(severity=sev, issue=kind, store=store, sku=sku, detail=detail, action=action))

    items = D["items"].copy()
    items = items[items.is_packaging == "0"].copy()
    items["mrp"] = items.mrp.astype(float)
    items["disp_cat"] = np.where(items.category.isin(["Safa", "Mala", "Stole"]), ACC, items.category)
    items = items.set_index("sku_id")

    stores = D["stores"].copy()
    stores["cap"] = stores.stock_capacity_units.astype(int)
    stores = stores.set_index("store_id")

    wh_lead, s2s = {}, {}
    for _, r in D["lead_times"].iterrows():
        route, days = r.route.lower(), int(r.days)
        if route.startswith("warehouse"):
            for z in stores.zone.unique():
                if f"{z.lower()} zone" in route:
                    wh_lead[z] = days
        elif "same city" in route:
            s2s["city"] = days
        elif "same zone" in route:
            s2s["zone"] = days
        elif "different zone" in route:
            s2s["other"] = days
    stores["wh_lead"] = stores.zone.map(wh_lead)
    if stores.wh_lead.isna().any():
        worst = max(wh_lead.values()) if wh_lead else 7
        for s in stores.index[stores.wh_lead.isna()]:
            issue("Lead time missing", s, "", f"No warehouse lead time for zone '{stores.zone[s]}'",
                  f"Assumed {worst} days (the longest route)", "Medium")
        stores["wh_lead"] = stores.wh_lead.fillna(worst)
    for k, v in (("city", 2), ("zone", 4), ("other", 7)):
        s2s.setdefault(k, v)

    snap = D["stock_snapshot"].copy()
    global WH  # the warehouse is the one stock location that is not a store
    whs = sorted(set(snap.location_id) - set(stores.index))
    if len(whs) != 1:
        raise DataError(f"Expected exactly one warehouse location in stock_snapshot (a location_id that is not a store); found {whs or 'none'}.")
    WH = whs[0]
    today = pd.Timestamp(snap.as_of_date.max())
    snap = snap[snap.sku_id.isin(items.index)].copy()
    wk = D["stock_weekly"]
    last = wk[wk.week_ending == today.strftime("%Y-%m-%d")][["store_id", "sku_id", "closing_on_hand"]]
    snap = snap.merge(last, left_on=["location_id", "sku_id"], right_on=["store_id", "sku_id"], how="left")
    snap["oh"] = snap.on_hand
    bad = snap.closing_on_hand.notna() & (snap.closing_on_hand != snap.on_hand)
    for _, r in snap[bad].iterrows():
        use = max(int(r.closing_on_hand), 0)
        issue("Stock records disagree", r.location_id, r.sku_id,
              f"Snapshot says {r.on_hand} on hand, weekly stock count for {today:%d %b} says {int(r.closing_on_hand)}"
              + (" and the item shows 0 days in stock for 4+ weeks" if r.closing_on_hand == 0 else ""),
              f"Planned with {use} (the physical weekly count). Not used as a donor. Store to recount.")
    snap.loc[bad, "oh"] = snap.loc[bad, "closing_on_hand"].clip(lower=0)
    neg = snap.oh < 0
    for _, r in snap[neg].iterrows():
        issue("Negative stock", r.location_id, r.sku_id, f"On hand is {r.oh}", "Treated as 0. Store to recount.")
    snap.loc[neg, "oh"] = 0
    snap["oh"] = snap.oh.astype(int)
    snap["age"] = (today - pd.to_datetime(snap.oldest_stock_received_on, errors="coerce")).dt.days

    new_stores = stores[pd.to_datetime(stores.opened_on) > today - pd.Timedelta(days=60)]
    for s, r in new_stores.iterrows():
        issue("New store", s, "", f"Opened {r.opened_on}: too little sales history for its own velocity",
              "Velocity blended with similar-tier stores; its own sales weigh in as they build up", "Info")

    prot = D["flagship_protected"].groupby("store_id").style_id.apply(set).to_dict()
    md = D["min_display"].set_index(["tier", "category"]).min_display_units.to_dict()
    am = D["area_managers"].set_index("area_manager_id")
    return dict(items=items, stores=stores, snap=snap, today=today, s2s=s2s, prot=prot, md=md, am=am,
                ex=ex, issue=issue)


# ----------------------------------------------------------------------------- 2. velocity
def sales_lines(D, P):
    b = D["bills"][["bill_id", "bill_date", "store_id"]]
    s = D["bill_lines"][["bill_id", "sku_id", "qty"]].merge(b, on="bill_id")
    s = s[s.sku_id.isin(P["items"].index)].copy()
    s["date"] = pd.to_datetime(s.bill_date)
    return s


def velocity(D, P, sales):
    items, stores, today = P["items"], P["stores"], P["today"]
    wk = D["stock_weekly"].copy()
    wk = wk[wk.sku_id.isin(items.index)]
    wk["week"] = pd.to_datetime(wk.week_ending)
    w0 = wk.week.min() - pd.Timedelta(days=6)
    s = sales[(sales.date >= w0) & (sales.date <= today)].copy()
    s["week"] = s.date + pd.to_timedelta((5 - s.date.dt.day_of_week) % 7, unit="D")  # Saturday week end
    sw = s.groupby(["store_id", "sku_id", "week"]).qty.sum().rename("sold").reset_index()
    f = wk.merge(sw, on=["store_id", "sku_id", "week"], how="outer").fillna({"sold": 0, "days_in_stock": 0})
    f["days"] = np.where((f.sold > 0) & (f.days_in_stock == 0), 1, f.days_in_stock)
    f["w"] = CFG["week_decay"] ** ((today - f.week).dt.days // 7)
    f["ws"], f["wd"] = f.w * f.sold, f.w * f.days
    g = f.groupby(["store_id", "sku_id"]).agg(ws=("ws", "sum"), wd=("wd", "sum"), ev=("days", "sum"),
                                              sold12=("sold", "sum")).reset_index()

    grid = pd.MultiIndex.from_product([stores.index, items.index], names=["store_id", "sku_id"]).to_frame(index=False)
    g = grid.merge(g, how="left").fillna({"ws": 0, "wd": 0, "ev": 0, "sold12": 0})
    chain = g.groupby("sku_id").agg(ws=("ws", "sum"), wd=("wd", "sum"))
    chain_rate = (chain.ws / chain.wd).where(chain.wd > 0)
    # never in stock anywhere in the window: fall back to the last 12 months of sales
    yr = sales[sales.date > today - pd.Timedelta(days=365)].groupby("sku_id").qty.sum()
    fb = (yr / (len(stores) * 365)).reindex(items.index).fillna(0)
    chain_rate = chain_rate.reindex(items.index).fillna(fb)
    g["chain_rate"] = g.sku_id.map(chain_rate)
    g["category"] = g.sku_id.map(items.category)
    g["tier"] = g.store_id.map(stores.tier)
    g["exp"] = g.wd * g.chain_rate
    sc = g.groupby(["store_id", "category"]).agg(num=("ws", "sum"), den=("exp", "sum"), tier=("tier", "first")).reset_index()
    sc["raw"] = (sc.num / sc.den).where(sc.den >= 3)
    tier_med = sc.groupby(["tier", "category"]).raw.median()
    sc["tm"] = [tier_med.get((t, c), 1.0) for t, c in zip(sc.tier, sc.category)]
    sc["tm"] = sc.tm.fillna(1.0)
    sc["m"] = ((sc.num + 3 * sc.tm) / (sc.den + 3)).clip(0.1, 5)
    g = g.merge(sc[["store_id", "category", "m"]], on=["store_id", "category"])
    g["prior"] = g.chain_rate * g.m
    own = (g.ws / g.wd).where(g.wd > 0, 0)
    K = CFG["shrink_days"]
    g["v"] = np.where(g.ev > 0, (own * g.ev + g.prior * K) / (g.ev + K), g.prior)
    # not yet launched -> no demand
    launch = pd.to_datetime(g.sku_id.map(items.launch_date), errors="coerce")
    g.loc[launch > today, "v"] = 0
    return g[["store_id", "sku_id", "v", "ws", "wd", "ev", "sold12", "chain_rate", "m"]], f


# ----------------------------------------------------------------------------- 3. season
class Season:
    """Day index per state: wedding dates and the week up to a festival sell more than normal days."""

    def __init__(self, D, P, sales):
        st = P["stores"]
        cal = D["calendar"].copy()
        cal["date"] = pd.to_datetime(cal.date)
        days = pd.date_range(sales.date.min(), cal.date.max())
        self.idx, self.lift = {}, {}
        ss = sales.assign(state=sales.store_id.map(st.state)).groupby(["state", "date"]).qty.sum()
        for state in st.state.unique():
            c = cal[cal.state == state].set_index("date").reindex(days)
            wed = c.is_wedding_date.eq("1")
            pre = pd.Series(False, index=days)
            for fd in c.index[c.festival.fillna("").ne("")]:
                pre[(days >= fd - pd.Timedelta(days=6)) & (days <= fd)] = True
            typ = pd.Series("normal", index=days)
            typ[pre] = "festival week"
            typ[wed] = "wedding date"
            typ[wed & pre] = "wedding + festival"
            units = ss.get(state, pd.Series(dtype=float)).reindex(days).fillna(0)
            hist = units.index <= P["today"]
            base = units[hist & (typ == "normal")].mean()
            lift = {t: (units[hist & (typ == t)].mean() / base if (hist & (typ == t)).sum() >= 10 else np.nan)
                    for t in typ.unique()}
            # too few days to trust a day type: fall back to the closest type with enough history
            lift["normal"] = 1.0
            for t, fb in (("festival week", "normal"), ("wedding date", "normal"), ("wedding + festival", "wedding date")):
                if t in lift and not lift[t] == lift[t]:
                    lift[t] = lift.get(fb, 1.0)
            self.lift[state] = lift
            self.idx[state] = typ.map(lift)
            self.typ = getattr(self, "typ", {})
            self.typ[state] = typ
        self.base_from = P["today"] - pd.Timedelta(days=83)
        self.today = P["today"]

    def mult(self, state, start, n_days):
        i = self.idx[state]
        fwd = i.reindex(pd.date_range(start, periods=n_days)).fillna(1.0).mean()
        back = i[self.base_from:self.today].mean()
        return float(np.clip(fwd / back, 0.5, 3.0))

    def next_event(self, state, start, n_days):
        t = self.typ[state].reindex(pd.date_range(start, periods=n_days)).fillna("normal")
        w = t[t.str.contains("wedding")]
        f = t[t.str.contains("festival")]
        out = []
        if len(f):
            out.append(f"festival week from {f.index[0]:%d %b}")
        if len(w):
            out.append(f"{len(w)} wedding dates from {w.index[0]:%d %b}")
        return "; ".join(out)


# ----------------------------------------------------------------------------- 4. plan
class Planner:
    def __init__(self, D, P, vel, season):
        self.P, self.season = P, season
        it, st, snap = P["items"], P["stores"], P["snap"]
        self.it, self.st = it, st
        self.run = P["today"] + pd.Timedelta(days=1)
        self.moves = []
        stock = snap[snap.location_id != WH].set_index(["location_id", "sku_id"])
        g = vel.copy()
        key = list(zip(g.store_id, g.sku_id))
        g["oh"] = [int(stock.oh.get(k, 0)) for k in key]
        g["transit"] = [int(stock.in_transit.get(k, 0)) for k in key]
        g["age"] = [stock.age.get(k, np.nan) for k in key]
        g["pos"] = g.oh + g.transit
        g["state"] = g.store_id.map(st.state)
        g["L"] = g.store_id.map(st.wh_lead).astype(int)
        H = CFG["cover_days"]
        mult_need = {(s, L): season.mult(s, self.run, L + H) for s, L in set(zip(g.state, g.L))}
        mult30 = {s: season.mult(s, self.run, 30) for s in g.state.unique()}
        g["fd"] = g.v * [mult_need[(s, L)] for s, L in zip(g.state, g.L)]   # daily demand over lead + cover
        g["fd30"] = g.v * g.state.map(mult30)                               # daily demand next 30 days
        g["target"] = g.fd * (g.L + H)
        g["need"] = np.maximum(0, (g.target - g.pos + 0.5).apply(math.floor)).astype(int)
        g["style"] = g.sku_id.map(it.style_id)
        g["dcat"] = g.sku_id.map(it.disp_cat)
        g["mrp"] = g.sku_id.map(it.mrp)
        self.g = g.set_index(["store_id", "sku_id"])
        self.sty_pos = g.groupby(["store_id", "style"]).pos.sum().to_dict()
        self.sty_fd30 = g.groupby(["store_id", "style"]).fd30.sum().to_dict()
        self.cat_pos = g.groupby(["store_id", "dcat"]).pos.sum().to_dict()
        self.inc, self.out = defaultdict(int), defaultdict(int)
        self.sty_out, self.cat_in, self.cat_out = defaultdict(int), defaultdict(int), defaultdict(int)
        self.room = {s: int(st.cap[s] - g[g.store_id == s].pos.sum()) for s in st.index}
        whs = snap[snap.location_id == WH].set_index("sku_id").oh
        inb = D["warehouse_inbound"].copy()
        inb["d"] = pd.to_datetime(inb.expected_date)
        inb = inb[inb.d <= self.run + pd.Timedelta(days=CFG["inbound_usable_days"])]
        self.supply = {}
        for k in it.index:
            lots = [(self.run, int(whs.get(k, 0)))] if whs.get(k, 0) > 0 else []
            lots += [(d, int(q)) for d, q in inb[inb.sku_id == k].groupby("d").qty.sum().items()]
            self.supply[k] = sorted(lots)
        self.wh_start = {k: sum(q for _, q in v) for k, v in self.supply.items()}

    # --- helpers
    def r(self, s, k):
        return self.g.loc[(s, k)]

    def lead(self, a, b):
        if a == WH:
            return int(self.st.wh_lead[b])
        if b == WH:
            return int(self.st.wh_lead[a])
        A, B = self.st.loc[a], self.st.loc[b]
        return self.P["s2s"]["city" if A.city == B.city else "zone" if A.zone == B.zone else "other"]

    def cur(self, s, k):
        return int(self.g.at[(s, k), "pos"]) + self.inc[(s, k)] - self.out[(s, k)]

    def cover(self, s, k, col="fd30"):
        fd = self.g.at[(s, k), col]
        return self.cur(s, k) / fd if fd > 1e-9 else float("inf")

    def give_cap(self, d, k):
        """Units store d may give away of SKU k without breaking any donor rule."""
        st = self.st.loc[d]
        row = self.g.loc[(d, k)]
        if st.tier == "S++" and row["style"] in self.P["prot"].get(d, set()):
            return 0
        if self.inc[(d, k)] > 0:
            return 0
        g1 = int(row.oh) - self.out[(d, k)] - math.ceil(row.fd30 * CFG["donor_min_cover"] - 1e-9)
        sfd = self.sty_fd30[(d, row["style"])]
        spos = self.sty_pos[(d, row["style"])] - self.sty_out[(d, row["style"])]
        g2 = math.floor(spos - CFG["donor_min_cover"] * sfd + 1e-9) if sfd > 1e-9 else 10**9
        minimum = self.P["md"].get((st.tier, row["dcat"]), 0)
        g3 = self.cat_pos[(d, row["dcat"])] + self.cat_in[(d, row["dcat"])] - self.cat_out[(d, row["dcat"])] - minimum
        return max(0, min(g1, g2, g3))

    def add(self, typ, frm, to, k, qty, dispatch, reason, prio):
        if qty <= 0:
            return
        if frm != WH:
            row = self.g.loc[(frm, k)]
            self.out[(frm, k)] += qty
            self.sty_out[(frm, row["style"])] += qty
            self.cat_out[(frm, row["dcat"])] += qty
            self.room[frm] += qty
        if to != WH:
            row = self.g.loc[(to, k)]
            self.inc[(to, k)] += qty
            self.cat_in[(to, row["dcat"])] += qty
            self.room[to] -= qty
        self.moves.append(dict(type=typ, from_id=frm, to_id=to, sku_id=k, qty=int(qty),
                               dispatch=dispatch, arrives=dispatch + pd.Timedelta(days=self.lead(frm, to)),
                               priority=prio, reason=reason))

    def prio(self, s, k, L):
        c = self.g.at[(s, k), "pos"] / max(self.g.at[(s, k), "fd"], 1e-9)
        return "High" if c < L else "Medium" if c < L + 14 else "Low"

    def ev(self, s, L):
        e = self.season.next_event(self.st.state[s], self.run, L + CFG["cover_days"])
        return f"; {e}" if e else ""

    # --- A. warehouse -> store (who gets what when there is not enough for everyone)
    def warehouse(self):
        g = self.g
        needy = g[g.need > 0].reset_index()
        order = needy.assign(val=needy.need * needy.mrp).groupby("sku_id").val.sum().sort_values(ascending=False)
        for k in order.index:
            lots = self.supply[k]
            if not lots:
                continue
            cands = needy[needy.sku_id == k]
            heap = []
            for _, r in cands.iterrows():
                heapq.heappush(heap, (r.pos / max(r.fd, 1e-9), TIER_RANK[self.st.tier[r.store_id]], r.store_id))
            got = defaultdict(lambda: defaultdict(int))
            short = 0
            while heap and lots:
                c, t, s = heapq.heappop(heap)
                if self.room[s] - sum(got[s].values()) <= 0:
                    continue
                d, q = lots[0]
                got[s][d] += 1
                lots[0] = (d, q - 1)
                if lots[0][1] == 0:
                    lots.pop(0)
                n = sum(got[s].values())
                if n < g.at[(s, k), "need"]:
                    heapq.heappush(heap, ((g.at[(s, k), "pos"] + n) / max(g.at[(s, k), "fd"], 1e-9), t, s))
            short = len(heap)
            for s, by_date in got.items():
                r = g.loc[(s, k)]
                L = int(r.L)
                tot = sum(by_date.values())
                cov = r.pos / r.fd if r.fd > 1e-9 else float("inf")
                txt = (f"{s} sells {r.fd:.2f}/day; holds {int(r.oh)}"
                       + (f" (+{int(r.transit)} in transit)" if r.transit else "")
                       + f" = {cov:.0f} days vs {L}-day lead{self.ev(s, L)}. "
                       + f"Send {tot} for {CFG['cover_days']} days' cover"
                       + (f" (WH short: shared across {short + len(got)} stores by lowest cover)" if short else "") + ".")
                for d, q in sorted(by_date.items()):
                    self.add(T_WH, WH, s, k, q, d, txt, self.prio(s, k, L))

    # --- min display top-up (warehouse first, then stores in B)
    def display_gaps(self):
        gaps = {}
        for (s, c), pos in self.cat_pos.items():
            m = self.P["md"].get((self.st.tier[s], c), 0)
            have = pos + self.cat_in[(s, c)] - self.cat_out[(s, c)]
            if have < m:
                gaps[(s, c)] = m - have
        return gaps

    def display_from_wh(self):
        for (s, c), gap in self.display_gaps().items():
            skus = self.g.loc[s][self.g.loc[s]["dcat"] == c].sort_values("fd", ascending=False).index
            sent = defaultdict(int)
            while gap > 0 and self.room[s] - sum(sent.values()) > 0:
                progress = False
                for k in skus:
                    if gap == 0:
                        break
                    if self.supply[k]:
                        d, q = self.supply[k][0]
                        self.supply[k][0] = (d, q - 1)
                        if q - 1 == 0:
                            self.supply[k].pop(0)
                        sent[(k, d)] += 1
                        gap -= 1
                        progress = True
                if not progress:
                    break
            m = self.P["md"][(self.st.tier[s], c)]
            for (k, d), q in sent.items():
                self.add(T_DISP, WH, s, k, q, d,
                         f"{s} {c} display is below the {self.st.tier[s]} minimum of {m}; topping up with its best-selling sizes.",
                         "High")

    # --- B. store -> store for what the warehouse could not cover
    def donors(self, s, k):
        out = []
        for d in self.st.index:
            if d == s:
                continue
            cap = self.give_cap(d, k)
            if cap > 0:
                age = self.g.at[(d, k), "age"]
                aged = 0 if (age == age and age >= CFG["age_days"]) else 1
                out.append((self.lead(d, s), aged, -self.cover(d, k), d, cap))
        return sorted(out)

    def transfers(self):
        g = self.g
        rows = []
        for (s, k), r in g[g.need > 0].iterrows():
            unmet = int(r.need) - self.inc[(s, k)]
            if unmet > 0:
                urgent = r.pos / max(r.fd, 1e-9) < r.L
                rows.append((not urgent, -unmet * r.fd * r.mrp, s, k, unmet))
        for _, _, s, k, unmet in sorted(rows):
            for L, _, d, cap in [(a, b, d, c) for a, b, _, d, c in self.donors(s, k)]:
                if unmet <= 0 or self.room[s] <= 0:
                    break
                q = min(unmet, cap, self.room[s], self.give_cap(d, k))
                if q <= 0:
                    continue
                r = g.loc[(s, k)]
                dc_before = self.cover(d, k)
                self.add(T_S2S, d, s, k, q, self.run, "", self.prio(s, k, L))
                sty = r["style"]
                sc = (self.sty_pos[(d, sty)] - self.sty_out[(d, sty)]) / max(self.sty_fd30[(d, sty)], 1e-9)
                cov = r.pos / r.fd if r.fd > 1e-9 else float("inf")
                self.moves[-1]["reason"] = (
                    f"WH can't cover it; {s} sells {r.fd:.2f}/day with {cov:.0f} days left{self.ev(s, L)}. "
                    f"{d} has {days(dc_before)} of this size and keeps {days(sc)} of the style after; arrives in {L} days.")
                unmet -= q
        # min display gaps the warehouse could not fill
        for (s, c), gap in self.display_gaps().items():
            skus = self.g.loc[s][self.g.loc[s]["dcat"] == c].sort_values("fd", ascending=False).index
            for k in skus:
                if gap <= 0 or self.room[s] <= 0:
                    break
                for L, _, _, d, cap in self.donors(s, k):
                    q = min(gap, 1, self.give_cap(d, k), self.room[s])
                    if q > 0:
                        m = self.P["md"][(self.st.tier[s], c)]
                        self.add(T_DISP, d, s, k, q, self.run,
                                 f"{s} {c} display below the {self.st.tier[s]} minimum of {m} and WH has none; "
                                 f"{d} can spare it (keeps 30+ days of the style).", "High")
                        gap -= q
                        break

    # --- C + D. ageing stock: move to where it sells, pull back what will not sell anywhere
    def ageing(self):
        g = self.g
        aged = g[(g.oh > 0) & (g.age >= CFG["age_days"])].reset_index()
        for _, a in aged.sort_values("age", ascending=False).iterrows():
            s, k = a.store_id, a.sku_id
            cov = self.cover(s, k)
            if cov <= CFG["age_cover"]:
                continue
            spare = self.give_cap(s, k)
            if spare <= 0:
                continue
            recips = []
            for r in self.st.index:
                if r == s or self.out[(r, k)] > 0:
                    continue
                rr = g.loc[(r, k)]
                if rr.fd < 0.05:   # must sell at least ~1.5 units a month to be worth the move
                    continue
                want = math.ceil(rr.fd * CFG["sell_within"]) - self.cur(r, k)
                if want > 0:
                    recips.append((-rr.fd, self.lead(s, r), r, want))
            for _, L, r, want in sorted(recips):
                q = min(spare, want, self.room[r], self.give_cap(s, k))
                if q <= 0:
                    continue
                rr = g.loc[(r, k)]
                self.add(T_AGE, s, r, k, q, self.run,
                         f"Held {int(a.age)} days at {s}, which sells {a.fd30:.2f}/day ({days(cov)} of stock); "
                         f"{r} sells {rr.fd:.2f}/day and will clear it within {CFG['sell_within']} days.", "Low")
                spare -= q
            if spare > 0 and self.cover(s, k) > CFG["pullback_cover"]:
                chain = g.xs(k, level="sku_id")
                # dead only if it sat on shelves (84+ store-days in stock) and still nobody bought it
                dead = chain.sold12.sum() == 0 and chain.ev.sum() >= 84
                # keep one of each size on display unless nobody anywhere is selling it
                q = min(spare, self.give_cap(s, k), self.cur(s, k) - self.g.at[(s, k), "transit"] - (0 if dead else 1))
                if q < (1 if dead else 2):
                    continue
                why = "no store has sold it in 12 weeks" if dead else "no store can sell it within 45 days"
                self.add(T_PULL, s, WH, k, q, self.run,
                         f"Held {int(a.age)} days at {s} ({days(self.cover(s, k))} of stock) and {why}; "
                         f"return {q} to WH" + (" (keeps 1 on display)" if not dead else "") + ".", "Low")

    def plan(self):
        self.warehouse()
        self.display_from_wh()
        self.transfers()
        self.ageing()
        return self.moves


# ----------------------------------------------------------------------------- 5. validate
def validate(moves, pl):
    """Re-check every rule from scratch against the final plan."""
    P, g, st = pl.P, pl.g, pl.st
    m = moves
    res = []

    def rule(name, bad, n, detail=""):
        res.append(dict(rule=name, checked=n, violations=len(bad), status="PASS" if not bad else "FAIL",
                        detail=detail if not bad else "; ".join(map(str, bad[:5]))))

    rule("Every move has a positive quantity and different from/to",
         m[(m.qty <= 0) | (m.from_id == m.to_id)].index.tolist(), len(m))
    rule("Every move is explained in one line",
         m[(m.reason.str.len() < 20) | m.reason.str.contains("\n")].index.tolist(), len(m))

    out = m[m.from_id != WH].groupby(["from_id", "sku_id"]).qty.sum()
    inn = m[m.to_id != WH].groupby(["to_id", "sku_id"]).qty.sum()
    bad = [k for k, q in out.items() if q > g.at[k, "oh"]]
    rule("Stores only give stock they physically hold (not in-transit)", bad, len(out))

    wh = m[m.from_id == WH].groupby("sku_id").qty.sum()
    rule("Warehouse never sends more than on hand + inbound due",
         [k for k, q in wh.items() if q > pl.wh_start.get(k, 0)], len(wh))

    final = g.pos.add(inn.rename_axis(["store_id", "sku_id"]), fill_value=0).sub(
        out.rename_axis(["store_id", "sku_id"]), fill_value=0)
    gf = g.assign(final=final)
    receivers = set(m[m.to_id != WH].to_id)
    tot = gf.groupby(level="store_id").final.sum()
    rule("No receiving store ends above its capacity (on hand + in transit + incoming)",
         [f"{s}: {int(tot[s])}>{st.cap[s]}" for s in receivers if tot[s] > st.cap[s]], len(receivers))

    givers = set(m[m.from_id != WH].from_id)
    cat = gf.groupby([gf.index.get_level_values(0), "dcat"]).final.sum()
    bad = [f"{s} {c}: {int(v)}<{P['md'][(st.tier[s], c)]}" for (s, c), v in cat.items()
           if s in givers and v < P["md"].get((st.tier[s], c), 0)]
    rule("Giving stores stay at or above minimum display per category", bad, len(givers))

    bad = [f"{r.from_id} {r.sku_id}" for r in m.itertuples() if r.from_id in st.index
           and st.tier[r.from_id] == "S++" and g.at[(r.from_id, r.sku_id), "style"] in P["prot"].get(r.from_id, set())]
    rule("S++ flagships never give away their top-20 styles", bad, int((m.from_id.map(st.tier) == "S++").sum()))

    s2s = m[(m.from_id != WH) & (m.to_id != WH)]
    sty = gf.groupby([gf.index.get_level_values(0), "style"]).agg(final=("final", "sum"), fd30=("fd30", "sum"))
    bad = []
    for d, sku in set(zip(s2s.from_id, s2s.sku_id)):
        r = sty.loc[(d, g.at[(d, sku), "style"])]
        if r.fd30 > 1e-9 and r.final / r.fd30 < CFG["donor_min_cover"] - 1e-6:
            bad.append(f"{d} {sku}: {r.final / r.fd30:.1f} days")
    rule("No store-to-store move leaves the giver under 30 days of style cover", bad, len(s2s))

    repl = m[m.type == T_WH]
    bad = [f"{r.to_id} {r.sku_id}" for r in repl.itertuples()
           if g.at[(r.to_id, r.sku_id), "pos"] >= g.at[(r.to_id, r.sku_id), "target"]]
    over = repl.groupby(["to_id", "sku_id"]).qty.sum()
    bad += [f"{s} {k} over-sent" for (s, k), q in over.items() if q > g.at[(s, k), "need"]]
    rule("In-transit stock counted: never replenish a size already covered by stock + transit", bad, len(repl))

    both = set(out.index) & set(inn.index)
    rule("No store both gives and receives the same SKU", sorted(both), len(out) + len(inn))
    big = m[m.value_mrp > CFG["approval_value"]]
    rule("Moves above Rs 1,00,000 at MRP are flagged for approval",
         big[big.approval != "Needs approval"].index.tolist(), len(big))
    return pd.DataFrame(res)


# ----------------------------------------------------------------------------- 6. findings + write
def findings(D, P, pl, vel_weekly):
    issue, g, it, st = P["issue"], pl.g, pl.it, pl.st
    # hidden stockouts: zero sales because zero stock
    recent = vel_weekly[vel_weekly.week > P["today"] - pd.Timedelta(days=28)].groupby(["store_id", "sku_id"]).days.sum()
    hs = g[(g.oh == 0) & (g.fd30 >= 0.05)].copy()
    hs["days4w"] = [recent.get(k, 0) for k in hs.index]
    hs = hs[hs.days4w == 0]
    for s, grp in hs.reset_index().groupby("store_id"):
        lost = (grp.fd30 * 30 * grp.mrp).sum()
        issue("Hidden stockout", s, f"{len(grp)} SKUs",
              f"{len(grp)} sizes that should sell {grp.fd30.sum() * 30:.0f} units in 30 days had 0 days in stock for 4 weeks "
              f"(zero sales = no stock, not no demand). ~Rs {lost / 1e5:.1f} lakh at risk.",
              "Velocity rebuilt from in-stock days and similar stores; refilled in this plan", "High")
    # chronically under-stocked stores
    w = vel_weekly[vel_weekly.week > P["today"] - pd.Timedelta(days=84)]
    held = w[w.store_id.isin(st.index)].groupby("store_id").agg(d=("days_in_stock", "mean"))
    sold = g.groupby(level="store_id").fd30.sum()
    for s in st.index:
        fill = g.loc[s].pos.sum() / st.cap[s]
        if held.d.get(s, 7) < 5 and fill < 0.6:
            issue("Under-stocked store", s, "",
                  f"Only {fill:.0%} of capacity used; sizes in stock {held.d[s]:.1f} of 7 days on average, "
                  f"yet it sells {sold[s]:.1f} units/day", "Priority refill in this plan", "High")
        if fill > 1:
            issue("Over capacity", s, "", f"Records show {fill:.0%} of capacity", "No warehouse sends until resolved", "High")
    # dead and new styles
    sold_all = D["bill_lines"].groupby("sku_id").qty.sum()
    for sty, grp in it.groupby("style_id"):
        stock = int(g[g["style"] == sty].oh.sum())
        if sold_all.reindex(grp.index).fillna(0).sum() == 0 and stock > 0:
            issue("Dead style", "", sty, f"{grp.style_name.iloc[0]}: never sold in 24 months, {stock} units in stores",
                  "Pull back to WH (see moves); stop buying", "Medium")
        ld = pd.to_datetime(grp.launch_date.iloc[0], errors="coerce")
        if ld == ld and ld > P["today"] - pd.Timedelta(days=60):
            issue("New launch", "", sty, f"{grp.style_name.iloc[0]} launched {ld:%d %b %Y}: little sales history",
                  "Not treated as slow; velocity measured only on days it was in stock", "Info")
    # warehouse category shortfall
    need = g.groupby("dcat").need.sum()
    whc = pd.Series(pl.wh_start).groupby(it.disp_cat).sum()
    for c in need.index:
        if need[c] > 2 * whc.get(c, 0) and need[c] >= 20:
            issue("Warehouse shortfall", WH, c,
                  f"Stores need {int(need[c])} {c} units for the next 4-5 weeks; WH has {int(whc.get(c, 0))} now",
                  "Filled store-to-store where donors have 30+ days of cover; chase supplier for the rest", "High")
    for (s, c), gap in pl.display_gaps().items():
        issue("Below minimum display after plan", s, c, f"Still {gap} units short of the {st.tier[s]} minimum",
              "No warehouse stock or eligible donor; flag to buying team", "High")


def am_alerts(P, mv, ex):
    st, am = P["stores"], P["am"]
    rows = []
    for a, ar in am.iterrows():
        cand = []
        my = [s.strip() for s in ar.stores.split(",")]
        for s in my:
            hi = mv[(mv.to_id == s) & (mv.priority == "High")]
            if len(hi):
                cand.append((hi.value_mrp.sum(), f"{s}: {hi.sku_id.nunique()} sizes about to run out; "
                                                 f"{int(hi.qty.sum())} units on the way (by {hi.arrives.max():%d %b}). Please receive and display."))
            outs = mv[(mv.from_id == s) & (mv.to_id != WH)]
            if len(outs):
                cand.append((outs.value_mrp.sum() * 0.8, f"{s}: pack {int(outs.qty.sum())} units for "
                                                       f"{outs.to_id.nunique()} stores today ({', '.join(sorted(outs.to_id.unique())[:3])})."))
            pb = mv[(mv.from_id == s) & (mv.to_id == WH)]
            if len(pb):
                cand.append((pb.value_mrp.sum() * 0.5, f"{s}: return {int(pb.qty.sum())} slow units to WH to free display space."))
            for e in ex[(ex.store == s) & (ex.severity == "High")].itertuples():
                cand.append((5e6, f"{s}: {e.issue}: {e.detail} -> {e.action}"))
        for i, (_, txt) in enumerate(sorted(cand, key=lambda x: -x[0])[: int(ar.max_alerts_per_week)], 1):
            rows.append(dict(area_manager=f"{a} {ar['name']}", rank=i, alert=txt))
    return pd.DataFrame(rows)


def write(out_dir, pl, mv, ex, val, alerts):
    out_dir.mkdir(parents=True, exist_ok=True)
    run = pl.run
    g = pl.g.reset_index()
    cols = ["move_id", "priority", "approval", "type", "from_id", "to_id", "style_name", "size", "sku_id", "qty",
            "value_mrp", "dispatch", "arrives", "reason"]
    view = mv[cols].copy()
    for c in ("dispatch", "arrives"):
        view[c] = view[c].dt.strftime("%d %b")
    routes = (mv.groupby(["type", "from_id", "to_id"])
              .agg(skus=("sku_id", "nunique"), units=("qty", "sum"), value_mrp=("value_mrp", "sum"),
                   high=("priority", lambda x: (x == "High").sum()), arrives=("arrives", "max"))
              .reset_index().sort_values(["type", "value_mrp"], ascending=[True, False]))
    routes["arrives"] = routes.arrives.dt.strftime("%d %b")
    routes.insert(0, "approve (Y/N)", "")
    summ = (mv.groupby("type").agg(moves=("qty", "size"), units=("qty", "sum"), value_mrp=("value_mrp", "sum"),
                                   high_priority=("priority", lambda x: (x == "High").sum()),
                                   need_approval=("approval", lambda x: (x != "").sum())).reset_index())
    vel = g[(g.pos > 0) | (g.fd30 > 0.01)][["store_id", "sku_id", "style", "dcat", "v", "fd", "fd30", "oh", "transit",
                                             "pos", "age", "L", "target", "need", "sold12", "ev"]].copy()
    vel.columns = ["store", "sku", "style", "category", "velocity/day (in-stock)", "forecast/day (lead+cover)",
                   "forecast/day (30d)", "on hand", "in transit", "position", "stock age (days)", "WH lead (days)",
                   "target units", "need", "units sold 12w", "in-stock days 12w"]
    vel["cover days"] = (vel.position / vel["forecast/day (30d)"]).replace(np.inf, np.nan).round(0)
    vel = vel.round(3)
    xl = out_dir / f"stock_mover_{run:%Y-%m-%d}.xlsx"
    with pd.ExcelWriter(xl, engine="openpyxl") as w:
        head = pd.DataFrame({"Stock Mover": [
            f"Plan for {run:%a %d %b %Y} (stock as of close {pl.P['today']:%d %b %Y})",
            f"{len(mv)} moves, {int(mv.qty.sum())} units, Rs {mv.value_mrp.sum() / 1e5:.1f} lakh at MRP",
            f"Rule checks: {(val.status == 'PASS').sum()} of {len(val)} passed",
            f"Data issues found: {len(ex)} (see 'Data issues')",
            "Approve by route on 'Routes'; every SKU line with its one-line reason is on 'Moves'."]})
        head.to_excel(w, sheet_name="Summary", index=False)
        summ.to_excel(w, sheet_name="Summary", index=False, startrow=8)
        routes.to_excel(w, sheet_name="Routes", index=False)
        view.to_excel(w, sheet_name="Moves", index=False)
        ex.to_excel(w, sheet_name="Data issues", index=False)
        alerts.to_excel(w, sheet_name="AM alerts", index=False)
        val.to_excel(w, sheet_name="Rule checks", index=False)
        vel.to_excel(w, sheet_name="Velocity", index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = "A2" if ws.title != "Summary" else None
            if ws.title != "Summary":
                ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 110 if ws.title != "Summary" else 90)
    view.to_csv(out_dir / f"moves_{run:%Y-%m-%d}.csv", index=False)
    ex.to_csv(out_dir / f"data_issues_{run:%Y-%m-%d}.csv", index=False)
    val.to_csv(out_dir / f"rule_checks_{run:%Y-%m-%d}.csv", index=False)
    return xl


def run(source, out_dir=None):
    """Run the whole pipeline on a CSV folder or Excel workbook. Returns everything the app needs."""
    D = load(source)
    P = prep(D)
    sales = sales_lines(D, P)
    vel, weekly = velocity(D, P, sales)
    season = Season(D, P, sales)
    pl = Planner(D, P, vel, season)
    cols = ["type", "from_id", "to_id", "sku_id", "qty", "dispatch", "arrives", "priority", "reason"]
    mv = pd.DataFrame(pl.plan(), columns=cols)
    it = P["items"]
    mv["style_name"] = mv.sku_id.map(it.style_name)
    mv["size"] = mv.sku_id.map(it["size"])
    mv["value_mrp"] = (mv.qty * mv.sku_id.map(it.mrp)).astype(int)
    mv["approval"] = np.where(mv.value_mrp > CFG["approval_value"], "Needs approval", "")
    order = {"High": 0, "Medium": 1, "Low": 2}
    mv = mv.sort_values(["type", "priority", "value_mrp"], key=lambda c: c.map(order) if c.name == "priority" else c,
                        ascending=[True, True, False]).reset_index(drop=True)
    mv.insert(0, "move_id", [f"M{i + 1:04d}" for i in range(len(mv))])
    val = validate(mv, pl)
    findings(D, P, pl, weekly)
    ex = pd.DataFrame(P["ex"]).sort_values("severity", key=lambda c: c.map({"High": 0, "Medium": 1, "Info": 2}))
    alerts = am_alerts(P, mv, ex)
    xl = write(Path(out_dir), pl, mv, ex, val, alerts) if out_dir else None
    return dict(D=D, P=P, sales=sales, weekly=weekly, season=season, pl=pl, moves=mv, issues=ex,
                checks=val, alerts=alerts, xlsx=xl, warehouse=WH)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out", default=str(HERE / "output"))
    a = ap.parse_args()
    R = run(a.data, a.out)
    pl, mv, val, ex, season, xl = R["pl"], R["moves"], R["checks"], R["issues"], R["season"], R["xlsx"]
    print(f"Stock Mover plan for {pl.run:%d %b %Y}")
    for state, lift in season.lift.items():
        print(f"  season lift {state}: " + ", ".join(f"{k} {v:.2f}" for k, v in lift.items()))
    print(mv.groupby("type").agg(moves=("qty", "size"), units=("qty", "sum"), value=("value_mrp", "sum")).to_string())
    print(val[["rule", "checked", "violations", "status"]].to_string(index=False))
    print(f"Data issues: {len(ex)}  ->  {xl}")
    if (val.status != "PASS").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
