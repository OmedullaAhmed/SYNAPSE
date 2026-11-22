"""One pipeline for every way a plan gets built: an upload in the app, a background job, or the daily automation.

    raw exports --ingest--> standard sheets --engine--> plan --save--> database (plan, lines, locations)
                                  |                         |
                                  +--> data-quality exceptions +--> stock-data exceptions
"""
import json
import os
import shutil
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import audit  # noqa: E402
# imported here, not inside build_plan: plans are built in background threads, where Streamlit has already put sys.path
# back and a first import of a project module would fail (silently losing buy suggestions, KPI history, the catalog)
import buying  # noqa: E402
import db  # noqa: E402
import kpi  # noqa: E402
import stockcount  # noqa: E402
import webhooks  # noqa: E402
import engine as E  # noqa: E402
import exceptions_queue as XQ  # noqa: E402
import feedback as fb  # noqa: E402
import ingest  # noqa: E402
import plans  # noqa: E402


# Plans shared by every session in this server process: twenty approvers opening the same daily plan hold one copy, and
# reopening a cached plan is instant. Least recently used plans are dropped first.
_CACHE, _CLOCK = OrderedDict(), threading.Lock()
CACHE_SIZE = int(os.environ.get("STOCKFLOW_PLAN_CACHE", "4"))


def cache_put(plan_id, R):
    with _CLOCK:
        _CACHE[plan_id] = R
        _CACHE.move_to_end(plan_id)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def cache_get(plan_id):
    with _CLOCK:
        R = _CACHE.get(plan_id)
        if R is not None:
            _CACHE.move_to_end(plan_id)
        return R


def housekeeping(hours=24):
    """Delete working folders older than a day (the Excel workbook of a plan opened earlier is simply not offered then)."""
    import time
    cut = time.time() - hours * 3600
    for d in Path(tempfile.gettempdir()).glob("stockflow_*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cut:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _apply_counts(std_dir, user):
    import pandas as pd

    price = {}
    f = Path(std_dir) / "items.csv"
    if f.exists():
        it = pd.read_csv(f, dtype=str, keep_default_na=False)
        if "mrp" in it:
            price = dict(zip(it.sku_id, pd.to_numeric(it.mrp, errors="coerce").fillna(0)))
    try:
        return stockcount.apply(std_dir, user, value_of=lambda s: float(price.get(s, 0)))
    except Exception as e:  # noqa: BLE001 - counts never block a plan
        return dict(applied=0, created=False, variances=[], error=f"{type(e).__name__}: {e}")


def build_plan(source, cfg, label, user, kind="upload", pack=None, progress=None, standardise=True, reopen=None, role=""):
    """Returns dict(R, plan_id, report). Raises engine.DataError when required data is missing."""
    say = progress or (lambda *_: None)
    housekeeping()
    work = Path(tempfile.mkdtemp(prefix="stockflow_std_"))
    rep = None
    try:
        src = Path(source)
        if standardise:
            say("Standardising the files", 0.01)
            rep = ingest.run(src, work / "std", cfg=cfg, user=user, source_label=label, log=not reopen, stale=kind not in ("demo",) and not reopen)
            if kind != "demo":                     # stock counts from photos, scans or the phone app
                rep["counts"] = _apply_counts(work / "std", user)
                if rep["counts"].get("created"):
                    rep["missing"] = [m for m in rep["missing"] if m != "stock_snapshot"]
            if rep["missing"]:
                if not reopen:
                    XQ.record(XQ.from_ingestion(rep), "data", user=user)
                raise E.DataError("\n".join(f"Missing sheet '{t}' (no file was recognised as {t})." for t in rep["missing"]))
            src = work / "std"

        def eng(step, share):
            say(step, 0.05 + 0.85 * share)
        R = E.run(src, work / "out", cfg, progress=eng)
        R["ingestion"] = rep
        R.pop("D", None)                    # raw input copies: not needed once planned (saves memory per plan)
        if reopen:
            cache_put(reopen, R)
            return dict(R=R, plan_id=reopen, report=rep)
        say("Saving the plan", 0.93)
        plan_id = fb.log_run(R, label, user)
        cache_put(plan_id, R)
        try:
            plans.save_plan(R, plan_id, label, user, kind, pack, cfg)
            save_error = ""
        except Exception as e:  # noqa: BLE001 - the plan is still usable in this session
            save_error = f"{type(e).__name__}: {e}"
        try:
            b = buying.suggestions(R)
            db.upsert_setting(f"buy:{plan_id}"[:40], json.loads(b.to_json(orient="records")), user)
        except Exception:  # noqa: BLE001
            pass
        try:
            kpi.save_snapshot(plan_id, R, user)
        except Exception:  # noqa: BLE001
            pass
        try:
            stockcount.save_catalog(R["P"]["items"], user)
        except Exception:  # noqa: BLE001
            pass
        try:
            XQ.save_locations(R, user)
            if rep is not None and rep.get("counts", {}).get("variances") is not None:
                XQ.record([dict(key=f"count:{v['location']}", kind="count", severity="high" if v["value"] > 10000 or v["full"] else "medium",
                                location=v["location"], ref=v["location"], title=f"Count differs from system at {v['location']}",
                                detail=f"{v['units']:,} units differ across {v['skus']} counted SKUs"
                                       + (" (full count: uncounted SKUs set to 0)" if v["full"] else "")
                                       + ". The plan used the counted figures.", value=v["value"])
                           for v in rep["counts"]["variances"]], "count", user=user)
            if rep is not None:
                XQ.record(XQ.from_ingestion(rep), "data", user=user)
            XQ.record(XQ.from_plan(R), "plan", user=user)
        except Exception:  # noqa: BLE001
            pass
        audit.log("plan.build", plan_id, dict(data=label[:80], trips=len(R["trips"]), lines=len(R["moves"]), source=kind,
                                              net=round(float(R["opportunity"]["net"])), stored_inputs=pack is not None or kind == "demo",
                                              data_issues=len(rep["issues"]) if rep else 0), user, role)
        try:
            webhooks.emit("plan.ready", {"plan_id": plan_id, "plan_date": f"{R['pl'].run:%Y-%m-%d}", "trips": len(R["trips"]),
                                         "pieces": int(R["moves"].qty.sum()), "source": kind})
        except Exception:  # noqa: BLE001
            pass
        say("Done", 1.0)
        return dict(R=R, plan_id=plan_id, report=rep, save_error=save_error)
    finally:
        # keep the Excel workbook the engine wrote (the app offers it for download), drop the standardised copies
        shutil.rmtree(work / "std", ignore_errors=True)


def std_inputs(plan_id, cfg=None):
    """A plan's input files, standardised, in a new temporary folder (the caller deletes it). None when not stored."""
    import io
    import zipfile

    import daily
    row = plans.get(plan_id) if plan_id else None
    if row is None:
        return None
    here = Path(__file__).resolve().parent
    raw = Path(tempfile.mkdtemp(prefix="stockflow_raw_"))
    std = Path(tempfile.mkdtemp(prefix="stockflow_std_"))
    try:
        if row.source == "demo":
            src = here / "data" / ("demo_general" if "Demo Mart" in str(row.data_label) else "demo")
        elif row.input_pack is not None:
            with zipfile.ZipFile(io.BytesIO(bytes(row.input_pack))) as z:
                for m in z.infolist():
                    p = Path(m.filename)
                    if not (m.is_dir() or p.is_absolute() or ".." in p.parts):
                        (raw / p.name).write_bytes(z.read(m))
            daily._unzip(raw)
            for j in raw.glob("*.json"):
                j.unlink()
            src = raw
        else:
            shutil.rmtree(std, ignore_errors=True)
            return None
        ingest.run(src, std, cfg=cfg, log=False, stale=False)
        return std
    finally:
        shutil.rmtree(raw, ignore_errors=True)
