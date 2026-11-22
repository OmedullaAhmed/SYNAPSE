"""Background jobs: plan builds and syncs run in a worker thread so the screen never freezes.

Every job is a row in the database (queued -> running -> done / failed / interrupted), with its current step and share
done, so progress is visible from any session and survives a page refresh. A job that was running when the server
restarted is marked "interrupted" on the next start; its inputs are saved with the plan, so it can simply be run again.

    job_id = jobs.submit("plan", fn, user="asha", label="Daily upload")   # fn(progress) -> result
    jobs.status(job_id)  -> dict(status, step, progress, plan_id, error …)
    jobs.result(job_id)  -> the return value of fn (kept in memory for this server process only)
"""
import os
import socket
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import db

HOST = f"{socket.gethostname()}:{os.getpid()}"
_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("STOCKFLOW_WORKERS", "2")), thread_name_prefix="stockflow-job")
_RESULTS, _LOCK = {}, threading.Lock()
_RECOVERED = False


def _upd(job_id, **v):
    try:
        db.update_rows("jobs", {"job_id": job_id}, v)
    except Exception:  # noqa: BLE001 - progress updates never break a job
        pass


def recover():
    """Mark jobs left 'queued'/'running' by an earlier process on this host as interrupted (called once per process)."""
    global _RECOVERED
    if _RECOVERED:
        return 0
    _RECOVERED = True
    try:
        d = db.read("jobs")
    except Exception:  # noqa: BLE001
        return 0
    if d.empty:
        return 0
    host = socket.gethostname()
    stale = d[d.status.isin(["queued", "running"]) & d.host.astype(str).str.startswith(host + ":") & (d.host != HOST)]
    for j in stale.job_id:
        _upd(j, status="interrupted", finished_at=datetime.now(), error="The server restarted while this was running. Run it again.")
    return len(stale)


def submit(kind, fn, user="", plan_id=None, label=""):
    recover()
    job_id = f"J{datetime.now():%y%m%d%H%M%S}-{uuid.uuid4().hex[:4]}"
    db.insert_rows("jobs", [dict(job_id=job_id, kind=kind, status="queued", requested_by=user, requested_at=datetime.now(),
                                 step=label[:120] or "Waiting for a free worker", progress=0.0, plan_id=plan_id, host=HOST, log="")])
    last = [0.0]

    def progress(step, share=None):
        now = time.time()
        if now - last[0] > 0.4 or (share or 0) >= 1:
            last[0] = now
            _upd(job_id, step=str(step)[:120], **({"progress": float(share)} if share is not None else {}))

    def work():
        _upd(job_id, status="running", started_at=datetime.now())
        try:
            res = fn(progress)
            with _LOCK:
                _RESULTS[job_id] = res
            forget_old()
            pid = res.get("plan_id") if isinstance(res, dict) else None
            _upd(job_id, status="done", finished_at=datetime.now(), progress=1.0, step="Done", **({"plan_id": pid} if pid else {}))
        except Exception as e:  # noqa: BLE001
            with _LOCK:
                _RESULTS[job_id] = e
            try:
                import sentry_sdk
                if type(e).__name__ != "DataError":       # bad input data is the user's to fix, not an error to page anyone about
                    sentry_sdk.capture_exception(e)
            except Exception:  # noqa: BLE001
                pass
            _upd(job_id, status="failed", finished_at=datetime.now(), error=f"{type(e).__name__}: {e}"[:2000],
                 log=traceback.format_exc()[-4000:])

    _POOL.submit(work)
    return job_id


def status(job_id):
    d = db.read("jobs", {"job_id": job_id})
    return d.iloc[0].to_dict() if len(d) else None


def result(job_id, pop=False):
    with _LOCK:
        return _RESULTS.pop(job_id, None) if pop else _RESULTS.get(job_id)


def recent(limit=30):
    d = db.read("jobs", order=["-requested_at"], limit=limit)
    return d.drop(columns=["log"], errors="ignore")


def busy():
    d = db.read("jobs")
    return 0 if d.empty else int(d.status.isin(["queued", "running"]).sum())


def active_for(plan_id):
    """A queued or running job for this plan in this server process, if any (so concurrent reopens share one build)."""
    if not plan_id:
        return None
    try:
        d = db.read("jobs", {"plan_id": plan_id})
    except Exception:  # noqa: BLE001
        return None
    d = d[d.status.isin(["queued", "running"]) & (d.host == HOST)] if len(d) else d
    return d.sort_values("requested_at").job_id.iloc[-1] if len(d) else None


def forget_old(max_items=4):
    """Keep only the most recent results in memory (plans themselves live in the pipeline cache and the database)."""
    with _LOCK:
        while len(_RESULTS) > max_items:
            _RESULTS.pop(next(iter(_RESULTS)))
