"""Stock counts (from photos, scans, the phone app or typed in) and how they feed the plan.

A saved count is a set of (location, SKU, quantity) for a day. When a plan is built for that day:
  - scope 'partial' (default): counted SKUs replace the system quantity at that location; other SKUs keep the system figure
  - scope 'full': the count is the whole truth for that location; SKUs not counted there are set to 0
  - no stock file at all: the counts become the stock file
Differences between count and system become 'Count differs from system' exceptions, with the value at stake.
"""
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import audit
import db


def catalog():
    try:
        return db.read("catalog")
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["sku_id", "name", "barcode", "category"])


def save_catalog(items, user=""):
    """Refresh the product catalog used to match photos and scans (called on every plan build)."""
    it = items.reset_index() if "sku_id" not in items.columns else items
    rows = [dict(sku_id=str(r.sku_id), name=str(getattr(r, "style_name", "") or "")[:200],
                 barcode=str(getattr(r, "barcode", "") or "")[:40], category=str(getattr(r, "category", "") or "")[:120],
                 updated_at=datetime.now()) for r in it.itertuples()]
    with db.engine().begin() as c:
        c.execute(db.T["catalog"].delete())
    for i in range(0, len(rows), 5000):
        db.insert_rows("catalog", rows[i:i + 5000])
    return len(rows)


def save(location, counted_on, lines, user, method="photo", source="", scope="partial", note=""):
    """lines: frame with sku_id, qty (+ confidence). Rows without a SKU are skipped. Returns (count_id, rows saved)."""
    cid = f"C{datetime.now():%y%m%d%H%M%S}-{uuid.uuid4().hex[:4]}"
    ok = lines[(lines.sku_id.astype(str) != "") & lines.qty.notna()]
    ok = ok.groupby("sku_id", as_index=False).agg(qty=("qty", "sum"), confidence=("confidence", "min")) if "confidence" in ok else \
        ok.groupby("sku_id", as_index=False).agg(qty=("qty", "sum")).assign(confidence=1.0)
    now = datetime.now()
    rows = [dict(count_id=cid, location_id=str(location).strip().upper(), sku_id=str(r.sku_id), qty=int(max(0, r.qty)),
                 counted_on=str(counted_on), counted_at=now, by=user, method=method[:40], source=source[:300],
                 confidence=float(r.confidence), scope=scope, note=note[:500]) for r in ok.itertuples()]
    if rows:
        db.insert_rows("stock_counts", rows)
    audit.log("count.save", cid, dict(location=location, day=str(counted_on), lines=len(rows), units=int(ok.qty.sum()), method=method,
                                      scope=scope), user, "")
    return cid, len(rows)


def counts(day=None, location=None):
    d = db.read("stock_counts")
    if d.empty:
        return d
    if day:
        d = d[d.counted_on == str(day)]
    if location:
        d = d[d.location_id == location]
    return d


def latest(day):
    """Latest count per (location, SKU) for a day, with each location's scope (full if any full count that day)."""
    d = counts(day)
    if d.empty:
        return d
    d = d.sort_values("counted_at").groupby(["location_id", "sku_id"], as_index=False).last()
    return d


def apply(std_dir, user="system", value_of=None):
    """Merge counts into the standardised stock file in std_dir. Returns dict(applied, created, variances=[...])."""
    std_dir = Path(std_dir)
    f = std_dir / "stock_snapshot.csv"
    out = dict(applied=0, created=False, variances=[])
    if f.exists():
        sn = pd.read_csv(f, dtype=str, keep_default_na=False)
        days = sorted(set(sn.as_of_date) - {""})
        day = days[-1] if days else None
    else:
        sn, day = None, None
    if day is None:                                       # no stock file: the most recent counted day becomes the stock
        all_ = db.read("stock_counts")
        if all_.empty:
            return out
        day = all_.counted_on.max()
    d = pd.concat([latest(day), latest((pd.Timestamp(day) + timedelta(days=1)).strftime("%Y-%m-%d"))]) if day else pd.DataFrame()
    if d.empty:
        return out
    d = d.sort_values("counted_at").groupby(["location_id", "sku_id"], as_index=False).last()
    if sn is None:
        sn = pd.DataFrame({"as_of_date": day, "location_id": d.location_id, "sku_id": d.sku_id, "on_hand": d.qty.astype(int).astype(str)})
        sn.to_csv(f, index=False)
        out.update(applied=len(d), created=True)
        return out
    sn["on_hand_num"] = pd.to_numeric(sn.on_hand, errors="coerce").fillna(0).astype(int)
    sell = sn.get("stock_status", pd.Series([""] * len(sn))).str.lower().isin(["", "sellable", "available", "ok"])
    key = list(zip(sn.location_id, sn.sku_id))
    sys_qty = sn[sell].groupby(["location_id", "sku_id"]).on_hand_num.sum().to_dict()
    full_locs = set(d[d.scope == "full"].location_id)
    for loc, grp in d.groupby("location_id"):
        counted = dict(zip(grp.sku_id, grp.qty.astype(int)))
        diff_units = diff_value = 0
        for sku, q in counted.items():
            diff_units += abs(q - sys_qty.get((loc, sku), 0))
            diff_value += abs(q - sys_qty.get((loc, sku), 0)) * (value_of(sku) if value_of else 0)
        if loc in full_locs:                              # not counted = not there
            for (l2, sku), q in sys_qty.items():
                if l2 == loc and sku not in counted and q:
                    diff_units += q
                    diff_value += q * (value_of(sku) if value_of else 0)
                    counted[sku] = 0
        # write: sellable rows of counted SKUs at this location get the counted figure (first row), others 0
        first = {}
        for i, (l2, sku) in enumerate(key):
            if l2 == loc and sku in counted and sell.iloc[i]:
                if sku not in first:
                    first[sku] = i
                    sn.iat[i, sn.columns.get_loc("on_hand")] = str(counted[sku])
                else:
                    sn.iat[i, sn.columns.get_loc("on_hand")] = "0"
        new = [s for s in counted if s not in first]
        if new:
            sn = pd.concat([sn, pd.DataFrame({"as_of_date": day, "location_id": loc, "sku_id": new,
                                               "on_hand": [str(counted[s]) for s in new]})], ignore_index=True)
        out["applied"] += len(counted)
        if diff_units:
            out["variances"].append(dict(location=loc, units=int(diff_units), value=float(diff_value), skus=len(grp),
                                         full=loc in full_locs))
    sn.drop(columns=["on_hand_num"]).fillna("").to_csv(f, index=False)
    return out
