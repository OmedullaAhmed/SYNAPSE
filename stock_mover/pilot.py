"""Pilot scorecard: pilot stores against comparison stores on the six pass marks agreed for a 60-day pilot.

Set up once (Operations tab or pilot.save_setup): start date, pilot stores, comparison stores, and the planners' weekly
hours on transfers before the pilot. The scorecard then reads what StockFlow already records: KPI snapshots saved with
every plan, released transfers and their receipts, line decisions, and the exceptions queue.

In-stock and old stock are compared as a difference-in-differences: (pilot now - pilot before) minus
(comparison now - comparison before), so a chain-wide good or bad month does not count for or against the pilot.
"""
from datetime import date, datetime, timedelta

import pandas as pd

import db
import feedback as fb

MARKS = [  # key, measure, pass rule (text)
    ("in_stock", "In-stock on selling items, pilot against comparison stores", "+3 points or more"),
    ("old_stock", "Old stock (units past the ageing limit), pilot against comparison stores", "10% lower or more"),
    ("on_time", "Transfers received complete and on time", "90% or more"),
    ("hours", "Planner hours spent on transfers", "half or less of before"),
    ("clean", "Trips approved without major line edits (last 2 weeks)", "80% or more"),
    ("errors", "Serious data errors unfixed for more than 2 working days", "none"),
]


def setup():
    return db.get_setting("pilot", {}) or {}


def save_setup(start, pilot_stores, comparison_stores, hours_before, hours_now=None, user=""):
    pilot_stores, comparison_stores = sorted(set(pilot_stores)), sorted(set(comparison_stores))
    if not pilot_stores:
        raise ValueError("Choose the pilot stores.")
    if set(pilot_stores) & set(comparison_stores):
        raise ValueError("A store cannot be in both groups.")
    db.upsert_setting("pilot", dict(start=str(start)[:10], pilot_stores=pilot_stores, comparison_stores=comparison_stores,
                                    hours_before=float(hours_before or 0), hours_now=None if hours_now in (None, "") else float(hours_now),
                                    by=user, at=datetime.now().isoformat(timespec="seconds")), user)


def set_hours_now(hours, user=""):
    s = setup()
    s["hours_now"] = float(hours)
    db.upsert_setting("pilot", s, user)


def _working_days(a, b):
    return int(sum(1 for i in range((b - a).days) if (a + timedelta(days=i + 1)).weekday() != 6))   # Sunday off


def _rate(h, stores, num, den):
    x = h[h.store_id.isin(stores)]
    return float(x[num].sum() / x[den].sum()) if len(x) and x[den].sum() else None


def _per_plan_mean(h, stores, col):
    x = h[h.store_id.isin(stores)]
    return float(x.groupby("plan_date")[col].sum().mean()) if len(x) else None


def scorecard(today=None, S=None):
    """dict(setup, rows=[dict(key, measure, target, pilot, comparison, result, passed, detail)], passed, of, day)."""
    S = S or setup()
    if not S.get("start"):
        return dict(setup=S, rows=[], passed=0, of=0, day=0)
    today = today or date.today()
    start = date.fromisoformat(S["start"])
    P, C = S["pilot_stores"], S.get("comparison_stores", [])
    rows = []
    h = db.read("kpi_history")
    if len(h):
        h["d"] = pd.to_datetime(h.plan_date).dt.date
        before = h[(h.d < start) & (h.d >= start - timedelta(days=28))]
        now = h[h.d >= max(start, today - timedelta(days=14))]
    else:
        before = now = h

    def did(num, den=None):
        f = (lambda x, s: _rate(x, s, num, den)) if den else (lambda x, s: _per_plan_mean(x, s, num))
        pb, pn, cb, cn = f(before, P), f(now, P), f(before, C), f(now, C)
        return pb, pn, cb, cn
    pb, pn, cb, cn = did("in_stock", "selling")
    if None not in (pb, pn):
        gain = (pn - pb) - ((cn - cb) if None not in (cb, cn) else 0)
        rows.append(dict(key="in_stock", pilot=f"{pb:.1%} → {pn:.1%}", comparison=f"{cb:.1%} → {cn:.1%}" if None not in (cb, cn) else "—",
                         result=f"{gain * 100:+.1f} points", passed=gain >= 0.03,
                         detail="" if None not in (cb, cn) else "No comparison stores data: pilot change only."))
    else:
        rows.append(dict(key="in_stock", pilot="—", comparison="—", result="needs plans before and after the start", passed=None, detail=""))
    pb, pn, cb, cn = did("old_units")
    if None not in (pb, pn) and pb:
        ch = pn / pb - 1
        cch = (cn / cb - 1) if None not in (cb, cn) and cb else 0
        rel = ch - cch
        rows.append(dict(key="old_stock", pilot=f"{pb:,.0f} → {pn:,.0f} units", comparison=f"{cb:,.0f} → {cn:,.0f} units" if cb else "—",
                         result=f"{rel:+.0%} against comparison", passed=rel <= -0.10, detail=""))
    else:
        rows.append(dict(key="old_stock", pilot="—", comparison="—", result="needs plans before and after the start", passed=None, detail=""))
    tr, _ = fb.tracking()
    rel_ = fb.released()
    if tr is not None and len(tr) and len(rel_):
        since = rel_.assign(day=pd.to_datetime(rel_.released_at, errors="coerce").dt.date)
        nums = set(since[since.day >= start].to_number)
        t = tr[tr.to_number.isin(nums) & (tr.to_location.isin(P) | tr.from_location.isin(P))]
        done = t[t.status.isin(["Received", "Received short", "Received over"])]
        ok = done[(done.status == "Received") & (pd.to_datetime(done.arrived_at, errors="coerce").dt.normalize()
                                                  <= pd.to_datetime(done.expected, errors="coerce"))]
        late_open = t[t.status.isin(["Late leaving", "Late arriving"])]
        base = len(done) + len(late_open)
        share = len(ok) / base if base else None
        rows.append(dict(key="on_time", pilot=f"{len(ok)} of {base}" if base else "—", comparison="", result=f"{share:.0%}" if share is not None else "no transfers yet",
                         passed=(share >= 0.9) if share is not None else None, detail=f"{len(late_open)} still late" if len(late_open) else ""))
    else:
        rows.append(dict(key="on_time", pilot="—", comparison="", result="no transfers released yet", passed=None, detail=""))
    hb, hn = S.get("hours_before") or 0, S.get("hours_now")
    rows.append(dict(key="hours", pilot=f"{hb:g} → {hn:g} h/week" if hn is not None else f"{hb:g} h/week before", comparison="",
                     result=f"{hn / hb:.0%} of before" if hn is not None and hb else "enter this week's hours",
                     passed=(hn <= hb / 2) if hn is not None and hb else None, detail=""))
    rec = db.read("recommendations")
    dec = db.read("trip_decisions")
    if len(rec) and len(dec):
        pl = db.read("plans")
        pdays = dict(zip(pl.plan_id, pd.to_datetime(pl.plan_date).dt.date)) if len(pl) else {}
        dec = dec[dec.plan_id.map(pdays).fillna(date.min) >= max(start, today - timedelta(days=14))]
        dec = dec[dec.decision == "approved"]
        r = rec.merge(dec[["plan_id", "to_number"]], on=["plan_id", "to_number"])
        r = r[r.to_id.isin(P) | r.from_id.isin(P)]
        if len(r):
            r["kept"] = pd.to_numeric(r.approved_qty, errors="coerce").fillna(pd.to_numeric(r.qty, errors="coerce"))
            r.loc[r.decision == "rejected", "kept"] = 0
            g = r.groupby(["plan_id", "to_number"]).agg(q=("qty", "sum"), k=("kept", "sum"))
            clean = (g.k >= 0.9 * g.q).mean()
            rows.append(dict(key="clean", pilot=f"{int((g.k >= 0.9 * g.q).sum())} of {len(g)} trips", comparison="", result=f"{clean:.0%}",
                             passed=clean >= 0.8, detail=""))
        else:
            rows.append(dict(key="clean", pilot="—", comparison="", result="no approved trips in the last 2 weeks", passed=None, detail=""))
    else:
        rows.append(dict(key="clean", pilot="—", comparison="", result="no approved trips yet", passed=None, detail=""))
    x = db.read("exceptions")
    if len(x):
        x = x[(x.status != "resolved") & (x.severity == "high") & x.kind.isin(["data", "plan", "count"])]
        old = [r for r in x.itertuples() if _working_days(pd.Timestamp(r.first_seen).date(), today) > 2]
        rows.append(dict(key="errors", pilot=f"{len(old)} open > 2 working days", comparison="", result="none" if not old else f"{len(old)} unfixed",
                         passed=not old, detail="; ".join(str(r.title)[:60] for r in old[:3])))
    else:
        rows.append(dict(key="errors", pilot="0", comparison="", result="none", passed=True, detail=""))
    meta = {k: (m, t) for k, m, t in MARKS}
    for r in rows:
        r["measure"], r["target"] = meta[r["key"]]
    return dict(setup=S, rows=rows, passed=sum(1 for r in rows if r["passed"]), of=len(rows),
                decided=sum(1 for r in rows if r["passed"] is not None), day=(today - start).days)


def verdict(sc):
    if not sc["rows"]:
        return "Set up the pilot first."
    if sc["day"] < 56:
        return f"Day {sc['day']} of 60: {sc['passed']} of {sc['of']} pass marks met so far ({sc['decided']} measurable)."
    return (f"Day {sc['day']}: {sc['passed']} of {sc['of']} pass marks met. "
            + ("Recommendation: go ahead with the full chain." if sc["passed"] >= 4 else "Recommendation: stop or extend; fewer than 4 marks met."))
