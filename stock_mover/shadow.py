"""Shadow mode (pilot weeks 1-2): planners keep deciding transfers as they do today; they upload what they actually
moved, and StockFlow compares it with what its plan would have done. Nothing is released.

    r = shadow.compare(actual_df, plan_id)          # or compare(actual_df, plan_moves=R["moves"], skipped=R["skipped"])
    r["summary"]  -> agreement, missed value, planner-only moves
    r["lines"]    -> one row per (from, to, SKU) with planned qty, actual qty and what it means

Matching: same from, to and SKU = agreed (up to the smaller qty). Same SKU to the same store from a different source
= "same need, other source" (half credit). The rest are plan-only (with the plan's value and reason) or planner-only
(with the plan's reason for not doing it, when it had one).
"""
import re
from datetime import datetime

import pandas as pd

import db

ALIASES = {
    "from_id": ["from", "from_id", "from_location", "source", "from_store", "from_site", "sender", "dispatch_from", "origin"],
    "to_id": ["to", "to_id", "to_location", "destination", "to_store", "to_site", "receiver", "ship_to"],
    "sku_id": ["sku", "sku_id", "item", "item_code", "itemcode", "product_code", "barcode", "article"],
    "qty": ["qty", "quantity", "pieces", "pcs", "units", "qty_sent", "sent"],
}


def _k(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def standardise(df):
    """Accepts the planners' own sheet layout (common column names); returns from_id, to_id, sku_id, qty."""
    cols = {_k(c): c for c in df.columns}
    out = {}
    for std, al in ALIASES.items():
        hit = next((cols[_k(a)] for a in al if _k(a) in cols), None)
        if hit is None:
            raise ValueError(f"No column for {std.replace('_id', '')} (looked for: {', '.join(al[:5])}…)")
        out[std] = df[hit]
    d = pd.DataFrame(out)
    for c in ("from_id", "to_id", "sku_id"):
        d[c] = d[c].astype(str).str.strip().str.upper()
    d["qty"] = pd.to_numeric(d.qty, errors="coerce").fillna(0).astype(int)
    d = d[(d.qty > 0) & (d.from_id != d.to_id)]
    return d.groupby(["from_id", "to_id", "sku_id"], as_index=False).qty.sum()


def compare(actual, plan_id=None, plan_moves=None, skipped=None):
    a = standardise(actual) if not {"from_id", "to_id", "sku_id", "qty"} <= set(actual.columns) else actual.copy()
    if plan_moves is None:
        plan_moves = db.read("recommendations", {"plan_id": plan_id})
        plan_moves = plan_moves.rename(columns={"grp": "group"})
    p = plan_moves.copy()
    for c in ("from_id", "to_id", "sku_id"):
        p[c] = p[c].astype(str).str.strip().str.upper()
    p["qty"] = pd.to_numeric(p.qty, errors="coerce").fillna(0).astype(int)
    p["net_benefit"] = pd.to_numeric(p.get("net_benefit", 0), errors="coerce").fillna(0)
    pg = p.groupby(["from_id", "to_id", "sku_id"], as_index=False).agg(planned=("qty", "sum"), value=("net_benefit", "sum"),
                                                                       reason=("reason", "first"))
    m = pg.merge(a.rename(columns={"qty": "actual"}), on=["from_id", "to_id", "sku_id"], how="outer").fillna(
        {"planned": 0, "actual": 0, "value": 0.0, "reason": ""})
    m["planned"], m["actual"] = m.planned.astype(int), m.actual.astype(int)
    m["agreed"] = m[["planned", "actual"]].min(axis=1)
    # same need (SKU into the same store), different source
    need_p = m[m.planned > m.agreed].groupby(["to_id", "sku_id"]).apply(lambda g: (g.planned - g.agreed).sum(), include_groups=False)
    need_a = m[m.actual > m.agreed].groupby(["to_id", "sku_id"]).apply(lambda g: (g.actual - g.agreed).sum(), include_groups=False)
    other = pd.concat([need_p.rename("p"), need_a.rename("a")], axis=1).dropna()
    other_src = set(other.index)
    sk = {}
    if skipped is not None and len(skipped):
        s = skipped.copy()
        for c in ("from_id", "to_id", "sku_id"):
            s[c] = s[c].astype(str).str.upper()
        sk = {(r.from_id, r.to_id, r.sku_id): str(r.skip_why) for r in s.itertuples()}

    def what(r):
        if r.planned and r.actual:
            return "Agreed" if r.planned == r.actual else ("Agreed, you moved more" if r.actual > r.planned else "Agreed, you moved fewer")
        if (r.to_id, r.sku_id) in other_src:
            return "Same need, other source"
        if r.planned:
            return "Plan only: not moved"
        why = sk.get((r.from_id, r.to_id, r.sku_id))
        return f"You only: plan skipped it ({why})" if why else "You only: not in the plan"
    m["result"] = m.apply(what, axis=1)
    plan_pcs, act_pcs = int(m.planned.sum()), int(m.actual.sum())
    agreed = int(m.agreed.sum())
    half = int(other.min(axis=1).sum()) if len(other) else 0
    missed = m[m.result == "Plan only: not moved"]
    summary = dict(
        plan_pieces=plan_pcs, actual_pieces=act_pcs, agreed_pieces=agreed,
        agreement=round((agreed + 0.5 * half) / plan_pcs, 3) if plan_pcs else None,
        planner_covered=round((agreed + 0.5 * half) / act_pcs, 3) if act_pcs else None,
        same_need_other_source=half,
        missed_value=float(missed.value.sum()), missed_lines=int(len(missed)), missed_pieces=int(missed.planned.sum()),
        planner_only_lines=int(m.result.str.startswith("You only").sum()),
        planner_only_pieces=int(m[m.result.str.startswith("You only")].actual.sum()))
    order = {"Plan only: not moved": 0, "Same need, other source": 1}
    m = m.sort_values(["result", "value"], key=lambda s: s.map(order).fillna(2) if s.name == "result" else -s).reset_index(drop=True)
    return dict(summary=summary, lines=m[["from_id", "to_id", "sku_id", "planned", "actual", "agreed", "value", "reason", "result"]])


def save(plan_id, summary, user):
    db.upsert_setting(f"shadow:{plan_id}"[:40], dict(summary, at=datetime.now().isoformat(timespec="seconds"), by=user), user)


def history():
    s = db.read("settings")
    if s.empty:
        return pd.DataFrame()
    s = s[s.key.str.startswith("shadow:")]
    return pd.DataFrame([dict(plan_id=k.split(":", 1)[1], **v) for k, v in zip(s.key, s.value) if isinstance(v, dict)])


def text(summary):
    s = summary
    if not s.get("plan_pieces"):
        return "The plan had no moves to compare."
    return (f"Your team moved {s['actual_pieces']:,} pieces; the plan proposed {s['plan_pieces']:,}. "
            f"{s['agreement']:.0%} of the plan matches what you did. "
            f"The plan had {s['missed_lines']:,} lines you didn't move ({s['missed_pieces']:,} pieces, worth ₹{s['missed_value']:,.0f} by its "
            f"estimate); you made {s['planner_only_lines']:,} moves it didn't propose.")
