"""Run history, ratings, suggested settings, released transfer orders and movements.

Storage is the database in db.py (PostgreSQL when configured, else a local SQLite file). The function names and
behaviour are unchanged from the earlier CSV version; older CSV files in ./workspace are imported once automatically."""
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

import db
import schema

DIR = Path(__file__).resolve().parent / "workspace"
RUNS, RATINGS, SETTINGS = DIR / "runs.csv", DIR / "ratings.csv", DIR / "settings.json"
ORDERS, RECEIPTS = DIR / "released_transfer_orders.csv", DIR / "receipts.csv"

SUGGESTIONS = {
    "Send more stock to stores (shelves ran empty)": (("planning", "cover_days"), +1),
    "Send less stock (shelves got too full)": (("planning", "cover_days"), -1),
    "Treat stock as old sooner": (("ageing", "age_days"), -1),
    "Treat stock as old later": (("ageing", "age_days"), +1),
    "Move old stock only to faster-selling stores": (("ageing", "sell_within"), -1),
    "Move old stock to more stores": (("ageing", "sell_within"), +1),
    "Bring back fewer pieces to the warehouse": (("ageing", "pullback_max_units_per_store"), -1),
    "Bring back more slow pieces per run": (("ageing", "pullback_max_units_per_store"), +1),
    "Too many small trips": (("economics", "min_trip_units"), +1),
    "Small urgent transfers were missed": (("economics", "min_trip_units"), -1),
}


_TABLE = {"runs.csv": "runs", "ratings.csv": "ratings", "released_transfer_orders.csv": "released_orders",
          "movements.csv": "movements", "whatsapp_log.csv": "whatsapp_log"}


def _append(path, rows):
    """Append rows to the table that stands for this (former) CSV file."""
    t = _TABLE.get(Path(path).name)
    if t:
        db.insert_rows(t, rows if isinstance(rows, (list, pd.DataFrame)) else [rows])


def _read(path):
    t = _TABLE.get(Path(path).name)
    if not t:
        return pd.DataFrame()
    d = db.read(t)
    return d.drop(columns=[c for c in ("id",) if c in d]) if len(d) else pd.DataFrame()


def key(sec_k):
    return f"{sec_k[0]}.{sec_k[1]}"


def load_settings():
    """Feedback-accepted overrides, as a nested dict to merge over the brand config."""
    try:
        v = db.get_setting("overrides", {})
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001  (database unreachable: run with standard settings)
        return {}


def save_settings(over, user=""):
    db.upsert_setting("overrides", over, user)


def reset_settings(user=""):
    db.upsert_setting("overrides", {}, user)


def log_run(R, label, user):
    mv, val = R["moves"], R["checks"]
    run_id = f"{R['pl'].run:%Y%m%d}-{uuid.uuid4().hex[:6]}"
    O = R["opportunity"]
    _append(RUNS, dict(run_id=run_id, run_at=datetime.now().strftime("%Y-%m-%d %H:%M"), user=user,
                       plan_date=f"{R['pl'].run:%Y-%m-%d}", data=label, trips=len(R["trips"]), lines=len(mv),
                       pieces=int(mv.qty.sum()), net_benefit=round(O["net"]), move_cost=round(O["move_cost"]),
                       rules_passed=int((val.status == "PASS").sum()), rules_total=len(val)))
    return run_id


def runs():
    return _read(RUNS)


def add_rating(run_id, stars, right, easy, time_, changes, comment, name):
    _append(RATINGS, dict(rated_at=datetime.now().strftime("%Y-%m-%d %H:%M"), run_id=run_id, stars=stars, moves_right=right,
                          easy=easy, saves_time=time_, changes="; ".join(changes), comment=(comment or "").strip(),
                          name=(name or "").strip()))


def ratings():
    r = _read(RATINGS)
    for c in ("stars", "moves_right", "easy", "saves_time"):
        if c in r:
            r[c] = pd.to_numeric(r[c], errors="coerce")
    return r


def suggested_settings(cfg):
    r = ratings()
    rows = []
    if r.empty:
        return pd.DataFrame(columns=["setting", "what it controls", "now", "suggested", "votes for", "votes against"])
    votes = {k: [0, 0] for k in schema.TUNABLE}
    for cell in r.changes.fillna(""):
        for label in [c.strip() for c in str(cell).split(";") if c.strip()]:
            if label in SUGGESTIONS:
                k, d = SUGGESTIONS[label]
                votes[k][0 if d > 0 else 1] += 1
    for k, (up, down) in votes.items():
        if up == down == 0:
            continue
        lo, hi, step, name = schema.TUNABLE[k]
        now = cfg[k[0]][k[1]]
        net = up - down
        new = min(hi, max(lo, now + (step if net > 0 else -step if net < 0 else 0)))
        rows.append({"setting": key(k), "what it controls": name, "now": now, "suggested": new,
                     "votes for": max(up, down), "votes against": min(up, down)})
    return pd.DataFrame(rows)


# ---- transfer orders released to the warehouse / ERP, and what was received
def release_orders(orders, user, run_id):
    """Logs the orders as released. Transfer numbers already released (by any plan) are skipped, so one transfer is
    never tracked twice. Returns the skipped numbers (empty when everything was released)."""
    done = released()
    seen = set(done.to_number) if len(done) else set()
    skip = sorted(set(orders.to_number) & seen)
    o = orders[~orders.to_number.isin(skip)]
    if len(o):
        o = o.assign(released_at=_now().strftime("%Y-%m-%d %H:%M"), released_by=user, run_id=run_id)
        _append(ORDERS, o.to_dict("records"))
    return skip


def released():
    o = _read(ORDERS)
    if len(o):
        o["qty"] = pd.to_numeric(o.qty, errors="coerce").fillna(0).astype(int)
    return o


EVENTS = DIR / "movements.csv"   # one row per SKU line per event: dispatched (left the source) or received
STATUS_ORDER = ["Waiting to leave", "Late leaving", "In transit", "Late arriving", "Received short", "Received over", "Received"]


def _now(tz="Asia/Kolkata"):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    except Exception:  # noqa: BLE001
        return datetime.now()


def events():
    e = _read(EVENTS)
    if len(e):
        e["qty"] = pd.to_numeric(e.qty, errors="coerce").fillna(0).astype(int)
    old = _read(RECEIPTS)       # receipts uploaded before movement tracking existed
    if len(old) and "qty_received" in old:
        old = old.rename(columns={"qty_received": "qty", "recorded_at": "at", "recorded_by": "by"}).assign(event="received", note="")
        old["qty"] = pd.to_numeric(old.qty, errors="coerce").fillna(0).astype(int)
        e = pd.concat([e, old[["to_number", "sku_id", "event", "qty", "at", "by", "note"]]], ignore_index=True)
    return e


def _record(event, to_number, user, lines=None, note="", tz="Asia/Kolkata", at=None):
    """lines: {sku_id: qty}; None = every line of the order at its planned (or dispatched) quantity."""
    o = released()
    o = o[o.to_number == to_number]
    if o.empty:
        raise ValueError(f"{to_number} has not been released.")
    if lines is None:
        base = o.groupby("sku_id").qty.sum()
        if event == "received":
            d = events()
            d = d[(d.to_number == to_number) & (d.event == "dispatched")]
            if len(d):
                base = d.groupby("sku_id").qty.sum()
        lines = base.to_dict()
    when = at or _now(tz).strftime("%Y-%m-%d %H:%M")
    _append(EVENTS, [dict(to_number=to_number, sku_id=k, event=event, qty=int(q), at=when, by=user, note=note)
                     for k, q in lines.items()])


def mark_dispatched(to_number, user, lines=None, note="", tz="Asia/Kolkata"):
    _record("dispatched", to_number, user, lines, note, tz)


def mark_received(to_number, user, lines=None, note="", tz="Asia/Kolkata"):
    _record("received", to_number, user, lines, note, tz)


def _upload(df, user, event, qty_col):
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    need = {"to_number", "sku_id", qty_col}
    if not need <= set(df.columns):
        raise ValueError(f"The file needs columns: {', '.join(sorted(need))}")
    df["qty"] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0).astype(int)
    date_col = next((c for c in ("dispatched_on", "received_on", "date", "at") if c in df.columns), None)
    when = df[date_col].astype(str) if date_col else _now().strftime("%Y-%m-%d %H:%M")
    out = pd.DataFrame({"to_number": df.to_number.astype(str).str.strip(), "sku_id": df.sku_id.astype(str).str.strip(),
                        "event": event, "qty": df.qty, "at": when, "by": user, "note": "uploaded"})
    _append(EVENTS, out.to_dict("records"))
    return len(out)


def add_dispatches(df, user):
    return _upload(df, user, "dispatched", "qty_shipped" if "qty_shipped" in [c.strip().lower() for c in df.columns] else "qty")


def add_receipts(df, user):
    return _upload(df, user, "received", "qty_received" if "qty_received" in [c.strip().lower() for c in df.columns] else "qty")


def tracking(today=None):
    """(orders, lines): one row per transfer order with its journey and status, and one per SKU line."""
    o = released()
    if o.empty:
        return pd.DataFrame(), pd.DataFrame()
    today = pd.Timestamp(today or _now().date())
    e = events()
    lines = o.groupby(["to_number", "from_location", "to_location", "sku_id", "ship_date", "expected_receipt"]).qty.sum() \
        .rename("planned").reset_index()
    for ev, col in (("dispatched", "sent"), ("received", "received")):
        if len(e):
            x = e[e.event == ev].groupby(["to_number", "sku_id"]).agg(**{col: ("qty", "sum"), f"{col}_at": ("at", "max")})
            lines = lines.merge(x, on=["to_number", "sku_id"], how="left")
        else:
            lines[col], lines[f"{col}_at"] = 0, None
    lines[["sent", "received"]] = lines[["sent", "received"]].fillna(0).astype(int)
    lines["gap"] = lines.received - lines.sent.where(lines.sent > 0, lines.planned)
    rel = o.groupby("to_number").released_at.min()
    t = lines.groupby(["to_number", "from_location", "to_location"]).agg(
        planned=("planned", "sum"), sent=("sent", "sum"), received=("received", "sum"), lines=("sku_id", "size"),
        ship_date=("ship_date", "first"), expected=("expected_receipt", "first"),
        left_at=("sent_at", "max"), arrived_at=("received_at", "max"),
        sent_lines=("sent_at", lambda x: int(x.notna().sum())), got_lines=("received_at", lambda x: int(x.notna().sum()))).reset_index()
    t["released_at"] = t.to_number.map(rel)
    ship, exp = pd.to_datetime(t.ship_date), pd.to_datetime(t.expected)

    def status(r, sd, ed):
        if r.got_lines > 0:
            base = r.sent if r.sent > 0 else r.planned
            return "Received short" if r.received < base else "Received over" if r.received > base else "Received"
        if r.sent_lines > 0:
            return "Late arriving" if ed < today else "In transit"
        return "Late leaving" if sd < today else "Waiting to leave"
    t["status"] = [status(r, sd, ed) for r, sd, ed in zip(t.itertuples(), ship, exp)]
    la, aa = pd.to_datetime(t.left_at, errors="coerce"), pd.to_datetime(t.arrived_at, errors="coerce")
    t["days_on_road"] = ((aa.fillna(pd.Timestamp(today)) - la).dt.total_seconds() / 86400).round(1).where(la.notna())
    t["planned_days"] = (exp - ship).dt.days
    t["status_rank"] = t.status.map({s: i for i, s in enumerate(STATUS_ORDER)})
    return t.sort_values(["status_rank", "to_number"]).drop(columns="status_rank").reset_index(drop=True), lines


def reconciliation():
    return tracking()[1]
