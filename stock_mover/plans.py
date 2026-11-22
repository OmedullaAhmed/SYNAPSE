"""Plans and their recommendations in the database: saved when built, line-level decisions, rebuild after a restart."""
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

import db

MAX_PACK_BYTES = 60 * 1024 * 1024   # input packs above this are not stored (the plan can then not be rebuilt after a restart)


def pack_uploads(uploads):
    """Bundle the uploaded files (as uploaded) into one zip, so the plan can be rebuilt exactly later."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in uploads:
            z.writestr(f.name.split("/")[-1], f.getvalue())
    b = buf.getvalue()
    return b if len(b) <= MAX_PACK_BYTES else None


def pack_dir(folder):
    """Zip a folder of input files (for the daily run), or None if over the size limit."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in Path(folder).rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(folder).as_posix())
    return buf.getvalue() if buf.tell() <= MAX_PACK_BYTES else None


def save_plan(R, plan_id, label, user, source, pack=None, cfg=None):
    mv, O, val = R["moves"], R["opportunity"], R["checks"]
    trips = R["trips"]
    db.insert_rows("plans", [dict(plan_id=plan_id, created_at=datetime.now(), created_by=user, plan_date=f"{R['pl'].run:%Y-%m-%d}",
                                  brand=R["cfg"]["brand"]["name"], data_label=label[:300], source=source, trips=len(trips), lines=len(mv),
                                  pieces=int(mv.qty.sum()), net_benefit=float(O["net"]), move_cost=float(O["move_cost"]),
                                  rules_passed=int((val.status == "PASS").sum()), rules_total=len(val),
                                  settings=json.loads(json.dumps(cfg or R["cfg"], default=str)), input_pack=pack, status="open")])
    to = trips.set_index("trip_key").to_number if len(trips) else pd.Series(dtype=str)
    rec = pd.DataFrame({"plan_id": plan_id, "move_id": mv.move_id, "to_number": mv.trip_key.map(to), "trip_key": mv.trip_key, "type": mv.type,
                        "grp": mv.group, "from_id": mv.from_id, "to_id": mv.to_id, "sku_id": mv.sku_id, "style_name": mv.style_name,
                        "size": mv["size"].astype(str), "qty": mv.qty.astype(int), "value_mrp": mv.value_mrp.astype(float),
                        "net_benefit": mv.net_benefit.astype(float), "priority": mv.priority, "reason": mv.reason, "decision": "pending",
                        "approved_qty": mv.qty.astype(int)})
    for i in range(0, len(rec), 5000):
        db.insert_rows("recommendations", rec.iloc[i:i + 5000])
    if len(trips):                                   # trips and transfer-order lines: release works without the plan in memory
        tt = pd.DataFrame({"plan_id": plan_id, "to_number": trips.to_number, "from_id": trips.from_id, "to_id": trips.to_id,
                           "dispatch": pd.to_datetime(trips.dispatch).dt.strftime("%Y-%m-%d"),
                           "arrives": pd.to_datetime(trips.arrives).dt.strftime("%Y-%m-%d"), "lines": trips.lines.astype(int),
                           "pieces": trips.pieces.astype(int), "value_mrp": trips.value_mrp.astype(float),
                           "net_benefit": trips.net_benefit.astype(float), "urgent": trips.urgent.astype(int),
                           "needs_approval": trips.needs_approval.astype(bool), "main_group": trips.main_group, "reason": trips.reason})
        db.insert_rows("plan_trips", tt)
    o = R.get("orders")
    if o is not None and len(o):
        cols = ["to_number", "line_no", "from_location", "to_location", "sku_id", "qty", "ship_date", "expected_receipt", "move_type",
                "reason_group", "move_id"]
        oo = o[[c for c in cols if c in o]].copy().assign(plan_id=plan_id)
        for c in ("ship_date", "expected_receipt"):
            if c in oo:
                oo[c] = pd.to_datetime(oo[c]).dt.strftime("%Y-%m-%d")
        for i in range(0, len(oo), 5000):
            db.insert_rows("plan_orders", oo.iloc[i:i + 5000])


def trips_of(plan_id):
    return db.read("plan_trips", {"plan_id": plan_id})


def orders_of(plan_id):
    o = db.read("plan_orders", {"plan_id": plan_id})
    return o.drop(columns=["id", "plan_id"], errors="ignore").sort_values(["to_number", "line_no"]) if len(o) else o


def recent(limit=15, user=None):
    d = db.read("plans", order=["-created_at"], limit=limit)
    if user is not None and len(d):
        d = d[d.created_by == user]
    if len(d):
        d = d.assign(has_inputs=d.input_pack.notna() | (d.source == "demo"))
    return d.drop(columns=["input_pack", "settings"]) if len(d) else d


def get(plan_id):
    d = db.read("plans", {"plan_id": plan_id})
    return d.iloc[0] if len(d) else None


def decisions(plan_id):
    return db.read("recommendations", {"plan_id": plan_id})


def set_decisions(plan_id, changes, user):
    """changes: rows with move_id, decision ('approved'|'rejected'|'pending'), approved_qty, note. Returns the rows that changed."""
    cur = decisions(plan_id).set_index("move_id")
    changed = []
    now = datetime.now()
    for r in changes.itertuples():
        if r.move_id not in cur.index:
            continue
        old = cur.loc[r.move_id]
        qty = int(max(0, min(int(old.qty), int(r.approved_qty))))
        dec = "rejected" if qty == 0 else r.decision
        note = r.note if isinstance(getattr(r, "note", ""), str) else ""
        old_note = old.note if isinstance(old.note, str) else ""
        if dec != old.decision or qty != int(old.approved_qty if old.approved_qty == old.approved_qty else old.qty) or note != old_note:
            db.update_rows("recommendations", {"plan_id": plan_id, "move_id": r.move_id},
                           dict(decision=dec, approved_qty=qty, note=note, decided_by=user, decided_at=now))
            changed.append(dict(move_id=r.move_id, sku_id=old.sku_id, route=f"{old.from_id}>{old.to_id}", from_decision=old.decision,
                                to_decision=dec, from_qty=int(old.approved_qty if old.approved_qty == old.approved_qty else old.qty),
                                to_qty=qty, recommended=int(old.qty), note=note))
    return changed


def apply_decisions(orders, plan_id):
    """Transfer-order lines after line decisions: rejected lines dropped, edited quantities applied, lines renumbered."""
    if orders is None or len(orders) == 0 or not plan_id:
        return orders
    d = decisions(plan_id)
    if d.empty:
        return orders
    d = d.set_index("move_id")
    o = orders.copy()
    o["decision"] = o.move_id.map(d.decision).fillna("pending")
    o["qty"] = o.move_id.map(d.approved_qty).fillna(o.qty).astype(int)
    o = o[(o.decision != "rejected") & (o.qty > 0)].drop(columns="decision")
    o["line_no"] = o.groupby("to_number").cumcount() + 1
    return o


def purge_inputs(days, user="retention"):
    """Data retention: drop stored input files (they hold sales lines) from plans older than `days`.
    The plan, its lines, decisions and audit stay; only 'reopen after restart' stops working for those plans."""
    import db
    from datetime import timedelta
    if not days or int(days) <= 0:
        return 0
    cut = datetime.now() - timedelta(days=int(days))
    d = db.read("plans")
    if d.empty:
        return 0
    old = d[(pd.to_datetime(d.created_at) < cut) & d.input_pack.notna()]
    for pid in old.plan_id:
        db.update_rows("plans", {"plan_id": pid}, {"input_pack": None})
    return len(old)
