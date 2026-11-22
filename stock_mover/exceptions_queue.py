"""Exceptions: data-quality alerts and unresolved stock discrepancies, kept open until someone resolves them.

Sources
  data      file checks at ingestion (stale stock, unknown SKUs, stores that sent nothing, sales gaps …)
  plan      data problems the planner found (stock records disagree, negative stock, unknown locations …)
  transfer  released transfers: late leaving, late arriving, received short or over (from dispatch/receipt events)

Each exception has a stable key, so the same problem seen on the next run is updated (seen_count, last_seen) rather
than duplicated. Problems that are no longer seen are closed automatically as "cleared"; people resolve the rest with a
note. Every resolution is in the audit trail.
"""
from datetime import datetime

import pandas as pd

import db

KINDS = {"data": "Data quality", "plan": "Stock data", "transfer": "Transfer discrepancy", "count": "Count vs system",
         "approval": "Approval overdue"}
# planner findings that are data problems someone has to fix at source (the rest are insights, not exceptions)
PLAN_KINDS = {"Stock records disagree": "high", "Negative stock": "high", "Unknown location": "high", "Cost price missing": "medium",
              "Stock age missing": "medium", "Non-sellable stock": "low", "Over capacity": "medium", "Warehouse shortfall": "medium",
              "Expiring soon": "high"}
TRANSFER_STATES = {"Late leaving": "medium", "Late arriving": "high", "Received short": "high", "Received over": "medium"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def locations():
    """{location_id: {zone, region, city}} saved with the latest plan; used to route exceptions and queues to regions."""
    try:
        return db.get_setting("locations", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_locations(R, user=""):
    st = R["pl"].st
    loc = {s: dict(zone=str(r.zone), region=str(r.region), city=str(r.city), type=str(r.store_type),
                   whatsapp=str(r.get("whatsapp", "") or "") if "whatsapp" in st else "") for s, r in st.iterrows()}
    for w in R["pl"].whs:
        wr = R["P"]["whs"].loc[w] if w in R["P"]["whs"].index else None
        loc[w] = dict(zone=str(getattr(wr, "zone", "") or ""), region="", city=str(getattr(wr, "city", "") or ""), type="warehouse",
                      whatsapp=str(getattr(wr, "whatsapp", "") or "") if wr is not None and "whatsapp" in wr.index else "")
    db.upsert_setting("locations", loc, user)
    return loc


def _zone(loc_id, locs=None):
    locs = locs if locs is not None else locations()
    return (locs.get(str(loc_id)) or {}).get("zone", "")


def record(items, source, close_missing=True, user="system"):
    """Upsert exceptions from one source. items: dicts with key, kind, severity, location, ref, title, detail, value.
    Open exceptions of this source that are not in items are closed as 'cleared' (the problem went away)."""
    now = datetime.now()
    cur = db.read("exceptions")
    open_ = cur[(cur.status.isin(["open", "assigned"])) & (cur.source == source)] if len(cur) else cur
    by_key = {r.key: r for r in open_.itertuples()} if len(open_) else {}
    locs = locations()
    new, seen = [], set()
    for it in items:
        k = str(it["key"])[:200]
        seen.add(k)
        if k in by_key:
            r = by_key[k]
            db.update_rows("exceptions", {"id": int(r.id)}, dict(last_seen=now, seen_count=int(r.seen_count or 1) + 1,
                                                                   detail=str(it.get("detail", ""))[:4000], value=float(it.get("value") or 0),
                                                                   severity=it.get("severity", r.severity), title=str(it["title"])[:1000]))
        else:
            loc = str(it.get("location", "") or "")
            new.append(dict(key=k, kind=it.get("kind", source), severity=it.get("severity", "medium"),
                            region=it.get("region") or _zone(loc.split(",")[0].strip(), locs), location=loc[:40],
                            ref=str(it.get("ref", ""))[:120], title=str(it["title"])[:1000], detail=str(it.get("detail", ""))[:4000],
                            value=float(it.get("value") or 0), first_seen=now, last_seen=now, seen_count=1, status="open",
                            owner="", source=source))
    if new:
        db.insert_rows("exceptions", new)
        try:
            import webhooks
            for n in new:
                if n["severity"] == "high":
                    webhooks.emit("exception.opened", {k: n[k] for k in ("key", "kind", "severity", "region", "location", "title", "value")})
        except Exception:  # noqa: BLE001
            pass
    closed = 0
    if close_missing:
        for k, r in by_key.items():
            if k not in seen:
                db.update_rows("exceptions", {"id": int(r.id)}, dict(status="cleared", resolved_by=user, resolved_at=now,
                                                                       resolution="No longer seen in the latest data"))
                closed += 1
    return dict(new=len(new), updated=len(seen) - len(new), cleared=closed)


def from_ingestion(rep):
    out = []
    for i in rep.get("issues", []):
        out.append(dict(key=f"data:{i['kind']}:{i['sheet']}", kind="data", severity=i["severity"], location=i.get("ref", "")
                        if i["kind"] in ("store_no_stock",) else "", ref=i["sheet"], title=i["title"], detail=i.get("detail", ""),
                        value=i.get("count", 0)))
    for f in rep.get("files", []):
        if f["status"] in ("unreadable", "not recognised"):
            out.append(dict(key=f"data:file:{f['file']}", kind="data", severity="medium", ref=str(f["file"]),
                            title=f"File {f['file']} was {f['status']}", detail="; ".join(f.get("notes", []))))
    for t in rep.get("missing", []):
        out.append(dict(key=f"data:missing:{t}", kind="data", severity="high", ref=t, title=f"Required sheet '{t}' was not received",
                        detail="The plan cannot run without it."))
    return out


def from_plan(R):
    ex = R.get("issues")
    if ex is None or len(ex) == 0:
        return []
    ex = ex[ex.issue.isin(PLAN_KINDS)]
    out = []
    for (kind, store), grp in ex.groupby(["issue", "store"], dropna=False):
        out.append(dict(key=f"plan:{kind}:{store}", kind="plan", severity=PLAN_KINDS[kind], location=str(store or ""),
                        ref=", ".join(grp.sku.astype(str).head(5)), title=f"{kind}" + (f" at {store}" if store else ""),
                        detail=" · ".join(grp.detail.astype(str).head(3)) + (f" (+{len(grp) - 3} more)" if len(grp) > 3 else ""),
                        value=float(grp.value.sum())))
    return out


def from_tracking(today=None):
    import feedback as fb
    t, _ = fb.tracking(today)
    if t is None or len(t) == 0:
        return []
    t = t[t.status.isin(TRANSFER_STATES)]
    out = []
    for r in t.itertuples():
        gap = int(r.received - (r.sent if r.sent > 0 else r.planned)) if r.status.startswith("Received") else 0
        detail = (f"{r.from_location} → {r.to_location}: planned {r.planned}, sent {r.sent}, received {r.received}"
                  + (f" ({gap:+d} pieces)" if gap else "") + f". Ship {r.ship_date}, due {r.expected}.")
        out.append(dict(key=f"transfer:{r.to_number}", kind="transfer", severity=TRANSFER_STATES[r.status],
                        location=r.to_location if r.status != "Late leaving" else r.from_location, ref=r.to_number,
                        title=f"{r.to_number} {r.status.lower()}", detail=detail, value=abs(gap)))
    return out


def sync_transfers(user="system"):
    return record(from_tracking(), "transfer", user=user)


def table(status=("open", "assigned")):
    d = db.read("exceptions")
    if d.empty:
        return d
    if status:
        d = d[d.status.isin(status)]
    d = d.assign(_s=d.severity.map(SEV_ORDER).fillna(3))
    return d.sort_values(["_s", "last_seen"], ascending=[True, False]).drop(columns="_s")


def resolve(ids, user, note):
    for i in ids:
        db.update_rows("exceptions", {"id": int(i)}, dict(status="resolved", resolved_by=user, resolved_at=datetime.now(), resolution=note[:2000]))


def assign(ids, owner):
    for i in ids:
        db.update_rows("exceptions", {"id": int(i)}, dict(status="assigned", owner=owner))


def summary():
    d = table()
    if d.empty:
        return dict(open=0, high=0, oldest_days=0)
    age = (pd.Timestamp.now() - pd.to_datetime(d.first_seen)).dt.days
    return dict(open=len(d), high=int((d.severity == "high").sum()), oldest_days=int(age.max()))


def check_deadlines(plan_id=None, now=None, approve_by=None, user="system"):
    """Regions with undecided trips after the approve-by time (plan day) -> 'Approvals overdue' exceptions (high if urgent
    refills are waiting). Cleared automatically once the region has decided. Returns the overdue regions."""
    import schema
    import plans as _plans
    from datetime import datetime as _dt
    now = now or _dt.now()
    if plan_id is None:
        p = _plans.recent(1)
        if p.empty:
            return []
        plan_id = p.iloc[0].plan_id
    p = _plans.get(plan_id)
    if p is None:
        return []
    hhmm = approve_by or ((p.settings or {}).get("operations", {}) or {}).get("approve_by") or schema.DEFAULTS["operations"]["approve_by"]
    deadline = _dt.fromisoformat(f"{p.plan_date}T{hhmm}")
    t = _plans.trips_of(plan_id)
    out = []
    if len(t) and now > deadline:
        d = db.read("trip_decisions", {"plan_id": plan_id})
        decided = set(d.to_number) if len(d) else set()
        import feedback as fb
        rel = fb.released()
        done = set(rel[rel.run_id == plan_id].to_number) if len(rel) and "run_id" in rel else set()
        locs = locations()
        t["region"] = [(locs.get(b) or {}).get("zone") or (locs.get(a) or {}).get("zone") or "—" for a, b in zip(t.from_id, t.to_id)]
        wait = t[~t.to_number.isin(decided | done)]
        late = (now - deadline).total_seconds() / 3600
        for region, g in wait.groupby("region"):
            urgent = int((g.urgent > 0).sum())
            out.append(dict(key=f"deadline:{plan_id}:{region}", kind="approval", severity="high" if urgent else "medium", region=region,
                            location="", ref=plan_id, title=f"Approvals overdue in {region}: {len(g)} trips",
                            detail=f"{len(g)} trips ({int(g.pieces.sum()):,} units, {urgent} urgent) not decided {late:.1f} h after the "
                                   f"{hhmm} cut-off for {p.plan_date}. Stock that should be moving is waiting.", value=float(g.net_benefit.sum())))
    record(out, "deadline", user=user)
    return [o["region"] for o in out]
