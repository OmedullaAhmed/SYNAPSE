"""Pilot-readiness checks: shadow mode, pilot scorecard, festival lift, full export and delete-on-exit.

    python tests/pilot_check.py            # throwaway SQLite database unless DATABASE_URL is set
"""
import io
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/pilot_check.db"

import pandas as pd  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import engine as E  # noqa: E402
import exceptions_queue as XQ  # noqa: E402
import feedback as fb  # noqa: E402
import kpi  # noqa: E402
import pilot as PILOT  # noqa: E402
import pipeline  # noqa: E402
import schema  # noqa: E402
import shadow  # noqa: E402

CHECKS, RESULTS, S = [], [], {}


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def setup():
    cfg = schema.load_config(HERE / "brands" / "demo_general.json")
    out = pipeline.build_plan(HERE / "data" / "demo_general", cfg, "Demo Mart (fictional general retailer)", "tester", kind="demo")
    S.update(R=out["R"], pid=out["plan_id"], cfg=cfg)


@check("Shadow mode: planners' own sheet layout is read and compared with the plan")
def t_shadow():
    mv = S["R"]["moves"]
    act = mv.sample(frac=.5, random_state=3)[["from_id", "to_id", "sku_id", "qty"]]
    act = act.rename(columns={"from_id": "From Store", "to_id": "Ship To", "sku_id": "Item Code", "qty": "Qty Sent"})
    act = pd.concat([act, pd.DataFrame({"From Store": ["WH-01"], "Ship To": [mv.to_id.iloc[0]], "Item Code": ["NOT-IN-PLAN"], "Qty Sent": [3]})])
    r = shadow.compare(act, S["pid"], skipped=S["R"]["skipped"])
    s = r["summary"]
    shadow.save(S["pid"], s, "tester")
    ok = 0.4 < s["agreement"] < 0.6 and s["planner_only_lines"] >= 1 and s["missed_lines"] > 0 and len(shadow.history()) == 1
    try:
        shadow.compare(pd.DataFrame({"a": [1]}), S["pid"])
        bad = False
    except ValueError:
        bad = True
    return ok and bad, shadow.text(s)


@check("Pilot scorecard: six marks computed from plans, transfers, decisions and exceptions")
def t_scorecard():
    R, pid = S["R"], S["pid"]
    stores = sorted(R["pl"].st.index)
    P, C = stores[:3], stores[3:6]
    h = db.read("kpi_history")
    day0 = pd.Timestamp(h.plan_date.iloc[0])
    later = h.drop(columns=["id"]).copy()
    later["plan_id"], later["plan_date"] = "later-plan", (day0 + pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    m = later.store_id.isin(P)
    later.loc[m, "in_stock"] = (later.loc[m, "selling"] * 0.98).round().astype(int)          # pilot stores improve
    later.loc[m, "old_units"] = (later.loc[m, "old_units"] * 0.7).round().astype(int)
    db.insert_rows("kpi_history", later)
    start = (day0 + pd.Timedelta(days=10)).date()
    PILOT.save_setup(start, P, C, hours_before=20, hours_now=8, user="tester")
    o = E.transfer_orders(R["moves"], R["trips"])
    tos = [t for t in o.to_number.unique() if o[o.to_number == t].to_location.iloc[0] in P][:4]
    fb.release_orders(o[o.to_number.isin(tos)], "tester", pid)
    for t in tos:
        fb.mark_dispatched(t, "tester")
        fb.mark_received(t, "tester")
    db.update_rows("plans", {"plan_id": pid}, {"plan_date": (day0 + pd.Timedelta(days=20)).strftime("%Y-%m-%d")})   # decisions made after the start
    db.insert_rows("trip_decisions", [dict(plan_id=pid, to_number=t, region="", decision="approved", by="tester") for t in tos])
    sc = PILOT.scorecard(today=(day0 + pd.Timedelta(days=21)).date())
    byk = {r["key"]: r for r in sc["rows"]}
    try:
        PILOT.save_setup(start, P, P[:1], 20)
        overlap = False
    except ValueError:
        overlap = True
    ok = (sc["of"] == 6 and byk["in_stock"]["passed"] and byk["old_stock"]["passed"] and byk["hours"]["passed"]
          and byk["on_time"]["result"] != "no transfers released yet" and byk["clean"]["passed"] and overlap)
    return ok, PILOT.verdict(sc) + " | " + "; ".join(f"{k}: {v['result']}" for k, v in byk.items())


@check("Pilot scorecard: a serious data error left open more than 2 working days fails the mark")
def t_errors():
    XQ.record([dict(key="data:test", kind="data", severity="high", location="", ref="", title="Stock file missing for 3 days",
                    detail="", value=0)], "data", user="tester")
    db.update_rows("exceptions", {"key": "data:test"}, {"first_seen": pd.Timestamp.now() - pd.Timedelta(days=6)})
    sc = PILOT.scorecard()
    r = {x["key"]: x for x in sc["rows"]}["errors"]
    return r["passed"] is False, r["result"]


@check("Festival lift: each named event learns its own lift per region from last year (hackathon calendar)")
def t_festival():
    cfg = schema.load_config()
    D = E.load(HERE / "data" / "csv", cfg)
    D2 = kpi._truncate(D, pd.Timestamp("2026-10-10"))
    P = E.prep(D2, cfg)
    se = E.Season(D2, P)
    named = {k: v for k, v in se.named.items() if v}
    any_reg = next(iter(named.values())) if named else {}
    cfg["season"]["named_events"] = False
    se0 = E.Season(D2, cfg and E.prep(D2, cfg))
    reg = next(iter(named))
    start = pd.Timestamp("2026-10-14")
    m1, m0 = se.mult(reg, start, 7), se0.mult(reg, start, 7)
    return len(named) >= 3 and "dussehra dasara" in any_reg and m1 != m0, \
        f"{len(named)} regions with named lifts; {reg}: Dussehra week multiplier {m0:.2f} -> {m1:.2f}"


@check("Full export: every table, uploaded files, no password or key hashes")
def t_export():
    auth.create_user("boss", "Strongpass123", "admin", must_change=False)
    z = zipfile.ZipFile(io.BytesIO(db.export_all()))
    names = z.namelist()
    users = pd.read_csv(z.open("users.csv"))
    ok = {"plans.csv", "recommendations.csv", "kpi_history.csv", "audit.csv", "README.txt"} <= set(names) and "password" not in users.columns
    return ok, f"{len(names)} files; users columns: {', '.join(users.columns[:4])}"


@check("Delete-on-exit: everything goes, accounts optionally kept, the deletion itself is audited")
def t_purge():
    n = db.purge_all("boss", keep_users=True)
    left = {t: len(db.read(t)) for t in ("plans", "recommendations", "kpi_history", "released_orders", "settings", "exceptions")}
    a = db.read("audit")
    ok = sum(n.values()) > 0 and not any(left.values()) and len(db.read("users")) >= 1 and list(a.action) == ["data.purge"]
    return ok, f"deleted {sum(n.values()):,} rows; left {left}; audit {list(a.action)}"


def main():
    t = time.time()
    setup()
    print(f"setup {time.time() - t:.0f}s")
    for name, fn in CHECKS:
        try:
            ok, ev = fn()
        except Exception as e:  # noqa: BLE001
            ok, ev = False, f"{type(e).__name__}: {e} | {traceback.format_exc().splitlines()[-3].strip()}"
        RESULTS.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name} — {str(ev)[:300]}", flush=True)
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
