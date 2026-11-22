"""Daily recommendation run, without anyone opening the app.

    python daily.py                       # uses the source saved in the app (Integrations tab) or [automation] in secrets
    python daily.py --source /data/drop   # a folder of the day's exports
    python daily.py --dry-run             # build and report, save nothing

Steps: pick up ERP/WMS confirmations -> fetch the day's exports -> standardise -> plan -> save the plan for approval
(nothing is released automatically) -> refresh exceptions -> record the job and an audit entry. Exit code 0 when a plan
was saved, 1 when the run failed (the reason is in the job record and on the Operations tab).

Schedule it with GitHub Actions (.github/workflows/daily-plan.yml), cron, or Windows Task Scheduler. The run is skipped if
a scheduled plan for the same planning day already exists, so a second trigger does no harm (use --force to rebuild).

Sources ([automation] in secrets or environment STOCKFLOW_AUTOMATION_*):
    source = "folder"   path = "/data/drop"                      (a mounted share or synced folder)
    source = "sftp"     host, port, username, password | private_key, remote_dir      (files are read, not deleted)
    source = "url"      urls = ["https://…/sales.csv", …]   token = "…"            (HTTP GET each file)
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import audit
import db
import safety
import erp
import exceptions_queue as XQ
import feedback as fb
import pipeline
import engine as E
import schema

HERE = Path(__file__).resolve().parent


def _secrets():
    """Streamlit secrets when run next to the app (.streamlit/secrets.toml), else empty."""
    for p in (HERE / ".streamlit" / "secrets.toml", HERE.parent / ".streamlit" / "secrets.toml"):
        if p.exists():
            try:
                import tomllib
                return tomllib.loads(p.read_text())
            except Exception:  # noqa: BLE001
                return {}
    return {}


def settings(secrets=None):
    """Non-secret automation settings saved in the app, overlaid with secrets / environment (which hold credentials)."""
    s = dict(db.get_setting("automation", {}) or {})
    sec = dict((secrets or {}).get("automation", {}) or {})
    s.update({k: v for k, v in sec.items() if v not in (None, "")})
    for k in ("source", "path", "urls", "token", "host", "username", "password", "remote_dir", "brand_profile", "run_at"):
        e = os.environ.get(f"STOCKFLOW_AUTOMATION_{k.upper()}")
        if e:
            s[k] = json.loads(e) if k == "urls" and e.strip().startswith("[") else e
    s.setdefault("source", "folder")
    s.setdefault("path", str(HERE / "workspace" / "drop"))
    s.setdefault("run_at", "06:30")
    return s


def fetch(s, dest):
    """Copy the day's raw files into dest. Returns a short description of the source."""
    dest.mkdir(parents=True, exist_ok=True)
    src = s["source"]
    if src == "url":
        urls = s.get("urls") or []
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()]
        for u in urls:
            req = urllib.request.Request(u)
            if s.get("token"):
                req.add_header("Authorization", "Bearer " + s["token"])
            with safety.urlopen(req, timeout=120) as r:   # noqa: S310 - URLs come from the admin's settings
                name = Path(u.split("?")[0]).name or f"file_{uuid.uuid4().hex[:6]}.csv"
                (dest / name).write_bytes(r.read())
        return f"{len(urls)} URLs"
    if src == "sftp":
        c = dict(s)
        c.setdefault("port", 22)
        t, sf = erp._sftp(c)
        try:
            rd = s.get("remote_dir", ".").rstrip("/")
            n = 0
            for f in sf.listdir(rd):
                if f.lower().endswith((".csv", ".xlsx", ".xls", ".zip", ".tsv", ".txt")):
                    sf.get(f"{rd}/{f}", str(dest / f))
                    n += 1
        finally:
            t.close()
        return f"SFTP {s.get('host')}:{rd} ({n} files)"
    p = Path(s["path"])
    if not p.exists():
        raise FileNotFoundError(f"Drop folder {p} does not exist")
    for f in p.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
        elif f.is_dir() and f.name not in ("processed", "archive"):
            shutil.copytree(f, dest / f.name)
    return f"folder {p}"


def _unzip(dest):
    import zipfile
    for z in list(dest.rglob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for m in zf.infolist():
                p = Path(m.filename)
                if m.is_dir() or p.is_absolute() or ".." in p.parts:
                    continue
                out = dest / "unzipped" / p
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(m))
        z.unlink()


def already_done(plan_date):
    try:
        p = db.read("plans")
        return len(p) > 0 and bool(((p.source == "scheduled") & (p.plan_date == plan_date)).any())
    except Exception:  # noqa: BLE001
        return False


def run_daily(user="automation", source=None, dry_run=False, force=False, progress=None, secrets=None):
    """The daily pipeline. Returns a summary dict (also stored on the job)."""
    say = progress or (lambda *_: None)
    secrets = secrets if secrets is not None else _secrets()
    s = settings(secrets)
    if source:
        s.update(source="folder", path=source)
    out = dict(started=datetime.now().isoformat(timespec="seconds"), source=s["source"])
    say("Picking up ERP / WMS confirmations", 0.02)
    out["erp_in"] = erp.poll(user, secrets)
    work = Path(tempfile.mkdtemp(prefix="stockflow_daily_"))
    try:
        say("Fetching the day's exports", 0.06)
        out["fetched_from"] = fetch(s, work / "raw")
        _unzip(work / "raw")
        cfg_path = HERE / "brands" / f"{s['brand_profile']}.json" if s.get("brand_profile") else None
        cfg = schema.load_config(cfg_path, fb.load_settings())
        if (work / "raw" / "config.json").exists():
            cfg = schema.merge(cfg, json.loads((work / "raw" / "config.json").read_text()))
        if dry_run:
            import ingest
            rep = ingest.run(work / "raw", work / "std", cfg=cfg, user=user, log=False)
            out.update(dry_run=True, sheets=rep["sheets"], issues=[i["title"] for i in rep["issues"]], missing=rep["missing"])
            return out
        import plans
        pack = None
        try:
            pack = plans.pack_dir(work / "raw")
        except Exception:  # noqa: BLE001
            pass
        res = pipeline.build_plan(work / "raw", cfg, f"Daily run {datetime.now():%d %b %Y} ({out['fetched_from']})"[:120], user,
                                  kind="scheduled", pack=pack, progress=lambda st, sh: say(st, 0.1 + 0.8 * sh), role="system")
        R = res["R"]
        pd_ = f"{R['pl'].run:%Y-%m-%d}"
        out.update(plan_id=res["plan_id"], plan_date=pd_, trips=len(R["trips"]), lines=len(R["moves"]), pieces=int(R["moves"].qty.sum()),
                   net=round(float(R["opportunity"]["net"])), data_issues=len(res["report"]["issues"]) if res["report"] else 0)
        say("Refreshing transfer exceptions", 0.95)
        out["transfer_exceptions"] = XQ.sync_transfers(user)
        keep = int((s.get("retention_days") or schema.DEFAULTS.get("privacy", {}).get("retention_days", 90)) or 0)
        out["inputs_purged"] = plans.purge_inputs(keep, user)
        import notify
        xs = XQ.summary()
        out["alerted"] = notify.post_webhook(notify.webhook_url(secrets), (
            f"StockFlow: plan for {pd_} is ready for approval — {out['trips']} trips, {out['pieces']:,} pieces, "
            f"net {E.money(out['net'], cfg)}. Open exceptions: {xs['open']} ({xs['high']} high)."))
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="folder of the day's exports (overrides the saved source)")
    ap.add_argument("--dry-run", action="store_true", help="check the files and report; save nothing")
    ap.add_argument("--force", action="store_true", help="build even if today's scheduled plan exists")
    a = ap.parse_args()
    import jobs
    secrets = _secrets()
    if not a.force and not a.dry_run:
        today = datetime.now().strftime("%Y-%m-%d")       # yesterday's closing stock plans today
        if already_done(today):
            print(f"A scheduled plan for {today} already exists; nothing to do (use --force to rebuild).")
            return 0
    job_id = jobs.submit("daily", lambda progress: run_daily("automation", a.source, a.dry_run, a.force, progress, secrets),
                         user="automation", label="Daily run")
    while True:
        st_ = jobs.status(job_id)
        if st_ and st_["status"] in ("done", "failed", "interrupted"):
            break
        time.sleep(1)
    res = jobs.result(job_id)
    if st_["status"] != "done":
        audit.log("automation.failed", job_id, {"error": st_.get("error")}, "automation", "system")
        import notify
        notify.post_webhook(notify.webhook_url(secrets), f"⚠️ StockFlow daily run failed: {str(st_.get('error'))[:300]}. "
                                                         "Open the Operations tab; the previous plan is unchanged.")
        print("FAILED:", st_.get("error"), file=sys.stderr)
        return 1
    audit.log("automation.run", res.get("plan_id", job_id), {k: v for k, v in res.items() if k not in ("erp_in",)}, "automation", "system")
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())


_SCHED = None


def start_scheduler(secrets=None):
    """Self-hosted servers only (STOCKFLOW_SCHEDULER=on): run the daily plan at the saved time from inside the app process.
    Streamlit Community Cloud sleeps idle apps, so use the GitHub Actions workflow there instead."""
    global _SCHED
    import threading
    if _SCHED is not None:
        return _SCHED

    def loop():
        import jobs
        while True:
            try:
                s = settings(secrets)
                hh, mm = map(int, str(s.get("run_at", "06:30")).split(":"))
                now = datetime.now()
                key = now.strftime("%Y-%m-%d")
                XQ.check_deadlines(user="scheduler")                # approvals overdue -> exceptions + alerts, every minute
                if (now.hour, now.minute) >= (hh, mm) and db.get_setting("scheduler_last", "") != key and not already_done(key):
                    db.upsert_setting("scheduler_last", key, "scheduler")      # claim the day first: one run even with two replicas
                    jobs.submit("daily", lambda p: run_daily("scheduler", None, False, False, p, secrets), "scheduler", label="Daily run")
            except Exception:  # noqa: BLE001 - the loop must never die
                pass
            time.sleep(60)
    _SCHED = threading.Thread(target=loop, daemon=True, name="stockflow-scheduler")
    _SCHED.start()
    return _SCHED
