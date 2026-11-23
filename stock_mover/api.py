"""StockFlow REST API (v1) for the phone app and for other systems.

    uvicorn api:app --host 0.0.0.0 --port 8000        # docs at /api/docs (OpenAPI), phone app at /app

Same database, users, roles and audit trail as the web app. Sign in with POST /api/v1/auth/token (user name + password;
lockout applies) or use an API key made by an admin (Authorization: Bearer sfk_...). Tokens last 12 hours.
Settings: STOCKFLOW_API_SECRET (token signing; set it in production), STOCKFLOW_CORS (extra allowed origins, comma separated).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets as pysecrets
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import audit
import auth
import db
import erp
import exceptions_queue as XQ
import feedback as fb
import jobs
import pipeline
import plans
import schema
import stockcount
import vision
import monitoring
import webhooks

monitoring.init("api")
HERE = Path(__file__).resolve().parent
VERSION = "1.0"


def _file_secrets():
    import daily
    return daily._secrets()


SECRETS = _file_secrets()                  # .streamlit/secrets.toml when present: same users / ERP settings as the web app
TOKEN_HOURS = 12
MAX_UPLOAD = 60 * 1024 * 1024

app = FastAPI(title="StockFlow API", version=VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json",
              description="Plans, approvals, releases, transfers, stock counts and exceptions. All writes are audited.")
origins = ["capacitor://localhost", "ionic://localhost", "http://localhost", "https://localhost", "http://localhost:5173"] + \
          [o.strip() for o in os.environ.get("STOCKFLOW_CORS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-store"
    return r


# ----------------------------------------------------------------------------- tokens and keys
def _secret():
    s = os.environ.get("STOCKFLOW_API_SECRET")
    if s:
        return s.encode()
    cur = db.get_setting("api_signing_secret")             # fallback: one random secret shared by every server process
    if not cur:
        cur = pysecrets.token_hex(32)
        db.upsert_setting("api_signing_secret", cur, "system")
    return cur.encode()


def make_token(user, role, stores=""):
    body = base64.urlsafe_b64encode(json.dumps({"u": user, "r": role, "s": stores, "exp": int(time.time()) + TOKEN_HOURS * 3600}).encode())
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), body, hashlib.sha256).digest()).rstrip(b"=")
    return (body.rstrip(b"=") + b"." + sig).decode()


def read_token(tok):
    try:
        body, sig = tok.encode().split(b".")
        pad = lambda b: b + b"=" * (-len(b) % 4)  # noqa: E731
        good = base64.urlsafe_b64encode(hmac.new(_secret(), pad(body), hashlib.sha256).digest()).rstrip(b"=")
        if not hmac.compare_digest(good, sig):
            return None
        d = json.loads(base64.urlsafe_b64decode(pad(body)))
        return d if d["exp"] > time.time() else None
    except Exception:  # noqa: BLE001
        return None


def _key_hash(k):
    return hashlib.sha256(k.encode()).hexdigest()


def make_api_key(name, role, stores, by):
    kid = "sfk_" + pysecrets.token_hex(4)
    raw = f"{kid}.{pysecrets.token_urlsafe(32)}"
    db.insert_rows("api_keys", [dict(key_id=kid, hash=_key_hash(raw), name=name[:120], role=role, stores=stores, created_by=by,
                                     created_at=datetime.now(), active=True)])
    audit.log("apikey.create", kid, {"name": name, "role": role}, by, "admin")
    return raw


class Who(BaseModel):
    user: str
    role: str
    stores: list[str] = []
    via: str = "token"


def who(authorization: Optional[str] = Header(None)) -> Who:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in first (Authorization: Bearer <token>).")
    tok = authorization.split(" ", 1)[1].strip()
    if tok.startswith("sfk_"):
        kid = tok.split(".")[0]
        d = db.read("api_keys", {"key_id": kid})
        if d.empty or not bool(d.iloc[0].active) or not hmac.compare_digest(d.iloc[0].hash, _key_hash(tok)):
            raise HTTPException(401, "API key not valid.")
        db.update_rows("api_keys", {"key_id": kid}, {"last_used": datetime.now()})
        r = d.iloc[0]
        return Who(user=f"api:{r['name']}", role=r.role, stores=[s.strip() for s in str(r.stores or "").split(",") if s.strip()], via="key")
    d = read_token(tok)
    if not d:
        raise HTTPException(401, "Session expired. Sign in again.")
    rec = auth.all_users(SECRETS).get(d["u"])
    if not rec or not rec.get("active", True):
        raise HTTPException(401, "This account is no longer active.")
    return Who(user=d["u"], role=rec.get("role", d["r"]), stores=[s.strip() for s in str(rec.get("stores", "") or "").split(",") if s.strip()])


def need(perm):
    def dep(w: Who = Depends(who)) -> Who:
        if not auth.allowed(w.role, perm):
            raise HTTPException(403, f"Your role ({w.role}) cannot do this.")
        return w
    return dep


def in_scope(w: Who, *locs):
    return not w.stores or bool(set(w.stores) & {str(x) for x in locs if x})


def rows(df):
    if df is None or len(df) == 0:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso", default_handler=str))


# ----------------------------------------------------------------------------- health and sign-in
@app.get("/api/v1/health", tags=["system"])
def health():
    try:
        name, pg = db.backend()
        db.read("settings", limit=1)
        return {"status": "ok", "version": VERSION, "database": name, "jobs_running": jobs.busy()}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": type(e).__name__}, status_code=503)


class TokenIn(BaseModel):
    username: str
    password: str


@app.post("/api/v1/auth/token", tags=["auth"])
def token(body: TokenIn):
    ok, rec, msg = auth.attempt(body.username, body.password, SECRETS)
    if not ok:
        audit.log("login.locked" if "locked" in msg.lower() or "too many" in msg.lower() else "login.failed", body.username.strip(),
                  {"via": "api"}, body.username.strip(), "")
        raise HTTPException(401, msg)
    if rec.get("must_change"):
        raise HTTPException(403, "Choose your own password in the web app first.")
    audit.log("login.ok", body.username.strip(), {"via": "api"}, body.username.strip(), rec.get("role", ""))
    return {"access_token": make_token(body.username.strip(), rec.get("role", "viewer"), rec.get("stores", "")), "token_type": "bearer",
            "expires_in": TOKEN_HOURS * 3600, "role": rec.get("role"), "user": body.username.strip()}


@app.get("/api/v1/me", tags=["auth"])
def me(w: Who = Depends(who)):
    return {**w.model_dump(), "permissions": sorted(auth.PERMS.get(w.role, set()))}


# ----------------------------------------------------------------------------- reference data
@app.get("/api/v1/locations", tags=["reference"])
def locations(w: Who = Depends(need("view"))):
    loc = XQ.locations()
    return [dict(id=k, **v) for k, v in sorted(loc.items()) if in_scope(w, k) or v.get("type") == "warehouse"]


@app.get("/api/v1/catalog", tags=["reference"])
def catalog(q: str = "", barcode: str = "", limit: int = 50, w: Who = Depends(need("view"))):
    c = stockcount.catalog()
    if barcode:
        c = c[(c.barcode == barcode.strip()) | (c.sku_id == barcode.strip().upper())]
    elif q:
        ql = q.lower()
        c = c[c.sku_id.str.lower().str.contains(ql, regex=False) | c.name.str.lower().str.contains(ql, regex=False)]
    return rows(c.drop(columns=["updated_at"], errors="ignore").head(min(limit, 500)))


# ----------------------------------------------------------------------------- plans and approvals
def _trip_state(plan_id):
    t = plans.trips_of(plan_id)
    if t.empty:
        return t
    locs = XQ.locations()
    t["region"] = [(locs.get(b) or {}).get("zone") or (locs.get(a) or {}).get("zone") or "—" for a, b in zip(t.from_id, t.to_id)]
    d = db.read("trip_decisions", {"plan_id": plan_id})
    dd = d.set_index("to_number") if len(d) else None
    t["decision"] = t.to_number.map(dd.decision) if dd is not None else None
    t["decision"] = t.decision.fillna("pending")
    rel = fb.released()
    done = set(rel[rel.run_id == plan_id].to_number) if len(rel) and "run_id" in rel else set()
    t["released"] = t.to_number.isin(done)
    return t


@app.get("/api/v1/plans", tags=["plans"])
def list_plans(limit: int = 20, w: Who = Depends(need("view"))):
    return rows(plans.recent(min(limit, 200)).drop(columns=["has_inputs"], errors="ignore"))


@app.get("/api/v1/plans/latest", tags=["plans"])
def latest_plan(w: Who = Depends(need("view"))):
    p = plans.recent(1)
    if p.empty:
        raise HTTPException(404, "No plans yet.")
    return plan(p.iloc[0].plan_id, w)


@app.get("/api/v1/plans/{plan_id}", tags=["plans"])
def plan(plan_id: str, w: Who = Depends(need("view"))):
    p = plans.get(plan_id)
    if p is None:
        raise HTTPException(404, "No such plan.")
    t = _trip_state(plan_id)
    if len(t) and w.stores:
        t = t[t.from_id.isin(w.stores) | t.to_id.isin(w.stores)]
    meta = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in p.drop(labels=["input_pack", "settings"]).to_dict().items()}
    summ = t.groupby("region").agg(trips=("to_number", "size"), pending=("decision", lambda x: int((x == "pending").sum())),
                                   pieces=("pieces", "sum")).reset_index() if len(t) else pd.DataFrame()
    return {"plan": meta, "regions": rows(summ), "trips": rows(t)}


@app.get("/api/v1/plans/{plan_id}/trips/{to_number}", tags=["plans"])
def trip_lines(plan_id: str, to_number: str, w: Who = Depends(need("view"))):
    d = plans.decisions(plan_id)
    d = d[d.to_number == to_number]
    if d.empty:
        raise HTTPException(404, "No such trip in this plan.")
    if not in_scope(w, d.from_id.iloc[0], d.to_id.iloc[0]):
        raise HTTPException(403, "Not one of your locations.")
    return rows(d[["move_id", "sku_id", "style_name", "size", "qty", "approved_qty", "decision", "net_benefit", "reason", "note", "decided_by"]])


class TripDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|pending)$")
    note: str = ""


@app.post("/api/v1/plans/{plan_id}/trips/{to_number}/decision", tags=["approvals"])
def decide_trip(plan_id: str, to_number: str, body: TripDecision, w: Who = Depends(need("approve"))):
    t = _trip_state(plan_id)
    t = t[t.to_number == to_number]
    if t.empty:
        raise HTTPException(404, "No such trip.")
    r = t.iloc[0]
    if not in_scope(w, r.from_id, r.to_id):
        raise HTTPException(403, "Not one of your locations.")
    if bool(r.released):
        raise HTTPException(409, "Already released.")
    db.delete_rows("trip_decisions", {"plan_id": plan_id, "to_number": to_number})
    db.insert_rows("trip_decisions", [dict(plan_id=plan_id, to_number=to_number, region=r.region, decision=body.decision, by=w.user,
                                           at=datetime.now(), note=body.note[:500])])
    audit.log("queue.decide", f"{plan_id}/{to_number}", {"before": r.decision, "after": body.decision, "via": w.via, "note": body.note[:200]},
              w.user, w.role)
    return {"ok": True, "to_number": to_number, "decision": body.decision}


class LineChange(BaseModel):
    move_id: str
    decision: str = Field("approved", pattern="^(approved|rejected|pending)$")
    approved_qty: int = Field(ge=0)
    note: str = ""


@app.post("/api/v1/plans/{plan_id}/lines", tags=["approvals"])
def decide_lines(plan_id: str, body: list[LineChange], w: Who = Depends(need("approve"))):
    d = plans.decisions(plan_id).set_index("move_id")
    bad = [c.move_id for c in body if c.move_id in d.index and not in_scope(w, d.at[c.move_id, "from_id"], d.at[c.move_id, "to_id"])]
    if bad:
        raise HTTPException(403, f"Not your locations: {', '.join(bad[:5])}")
    ch = pd.DataFrame([c.model_dump() for c in body])
    changed = plans.set_decisions(plan_id, ch, w.user) if len(ch) else []
    for c in changed:
        audit.log("decision.line", f"{plan_id}/{c['move_id']}", {**c, "via": w.via}, w.user, w.role)
    return {"changed": len(changed)}


class ReleaseIn(BaseModel):
    to_numbers: list[str] = []          # empty = every approved, unreleased trip you may release


@app.post("/api/v1/plans/{plan_id}/release", tags=["approvals"])
def release(plan_id: str, body: ReleaseIn, w: Who = Depends(need("release"))):
    t = _trip_state(plan_id)
    if t.empty:
        raise HTTPException(404, "No trips stored for this plan.")
    t = t[~t.released & (t.decision != "rejected")]
    t = t[t.to_number.isin(body.to_numbers)] if body.to_numbers else t[t.decision == "approved"]
    t = t[pd.Series([in_scope(w, a, b) for a, b in zip(t.from_id, t.to_id)], index=t.index, dtype=bool)]
    if t.empty:
        raise HTTPException(409, "Nothing approved to release.")
    o = plans.orders_of(plan_id)
    o = plans.apply_decisions(o[o.to_number.isin(t.to_number)], plan_id) if len(o) else o
    if o is None or o.empty:
        raise HTTPException(409, "Nothing approved to release.")
    skipped = fb.release_orders(o, w.user, plan_id)
    o = o[~o.to_number.isin(skipped)]
    if o.empty:
        raise HTTPException(409, "These transfer orders were already released.")
    audit.log("orders.release", plan_id, {"orders": int(o.to_number.nunique()), "lines": len(o), "pieces": int(o.qty.sum()), "via": w.via},
              w.user, w.role)
    sent = None
    if os.environ.get("STOCKFLOW_ERP_CHANNEL") or SECRETS.get("erp"):
        ok, msg = erp.export_orders(o, plan_id, w.user, SECRETS)
        sent = {"ok": ok, "message": msg}
        audit.log("erp.export", plan_id, {"ok": ok, "via": w.via}, w.user, w.role)
    webhooks.emit("trips.released", {"plan_id": plan_id, "to_numbers": sorted(o.to_number.unique()), "pieces": int(o.qty.sum())})
    return {"released": sorted(o.to_number.unique()), "lines": len(o), "pieces": int(o.qty.sum()), "erp": sent}


@app.get("/api/v1/plans/{plan_id}/purchase-suggestions", tags=["plans"])
def purchase_suggestions(plan_id: str, w: Who = Depends(need("view"))):
    """What to buy from suppliers because no stock exists anywhere to move (saved with each plan)."""
    return db.get_setting(f"buy:{plan_id}"[:40], []) or []


# ----------------------------------------------------------------------------- transfers
@app.get("/api/v1/transfers", tags=["transfers"])
def transfers(status: str = "", location: str = "", w: Who = Depends(need("view"))):
    t, _ = fb.tracking()
    if t is None or len(t) == 0:
        return []
    if w.stores:
        t = t[t.from_location.isin(w.stores) | t.to_location.isin(w.stores)]
    if location:
        t = t[(t.from_location == location) | (t.to_location == location)]
    if status:
        t = t[t.status.isin([s.strip() for s in status.split(",")])]
    return rows(t)


@app.get("/api/v1/transfers/{to_number}", tags=["transfers"])
def transfer(to_number: str, w: Who = Depends(need("view"))):
    t, lines = fb.tracking()
    if t is None or not len(t) or to_number not in set(t.to_number):
        raise HTTPException(404, "Not released.")
    r = t[t.to_number == to_number].iloc[0]
    if not in_scope(w, r.from_location, r.to_location):
        raise HTTPException(403, "Not one of your locations.")
    return {"order": rows(t[t.to_number == to_number])[0], "lines": rows(lines[lines.to_number == to_number])}


class MoveIn(BaseModel):
    lines: Optional[dict[str, int]] = None   # {sku_id: qty}; leave out = everything as planned / as sent
    note: str = ""


def _move(event, to_number, body, w):
    t, _ = fb.tracking()
    if t is None or not len(t) or to_number not in set(t.to_number):
        raise HTTPException(404, "Not released.")
    r = t[t.to_number == to_number].iloc[0]
    end = r.from_location if event == "dispatched" else r.to_location
    if not (auth.allowed(w.role, "track_all") or (auth.allowed(w.role, "track_own") and end in w.stores)):
        raise HTTPException(403, f"Only {end} (or an approver) can record this.")
    (fb.mark_dispatched if event == "dispatched" else fb.mark_received)(to_number, w.user, body.lines, body.note)
    audit.log(f"move.{event}", to_number, {"lines": len(body.lines or {}), "via": w.via}, w.user, w.role)
    webhooks.emit(f"transfer.{event}", {"to_number": to_number, "by": w.user, "lines": body.lines})
    if event == "received":
        XQ.sync_transfers(w.user)
    return {"ok": True}


@app.post("/api/v1/transfers/{to_number}/dispatch", tags=["transfers"])
def dispatch(to_number: str, body: MoveIn, w: Who = Depends(need("view"))):
    return _move("dispatched", to_number, body, w)


@app.post("/api/v1/transfers/{to_number}/receive", tags=["transfers"])
def receive(to_number: str, body: MoveIn, w: Who = Depends(need("view"))):
    return _move("received", to_number, body, w)


# ----------------------------------------------------------------------------- stock counts and images
class CountLine(BaseModel):
    sku_id: str = ""
    barcode: str = ""
    qty: int = Field(ge=0)


class CountIn(BaseModel):
    location_id: str
    counted_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope: str = Field("partial", pattern="^(partial|full)$")
    method: str = "app"
    lines: list[CountLine]


@app.post("/api/v1/counts", tags=["stock counts"])
def save_count(body: CountIn, w: Who = Depends(need("view"))):
    if not (auth.allowed(w.role, "track_all") or (auth.allowed(w.role, "track_own") and body.location_id in w.stores)):
        raise HTTPException(403, "You can count only your own locations.")
    cat = stockcount.catalog()
    by_bar = dict(zip(cat.barcode, cat.sku_id)) if len(cat) else {}
    df = pd.DataFrame([dict(sku_id=(l.sku_id or by_bar.get(l.barcode, "")).strip(), qty=l.qty) for l in body.lines])
    unknown = [l.barcode or l.sku_id for l in body.lines if not (l.sku_id or by_bar.get(l.barcode))]
    cid, n = stockcount.save(body.location_id, body.counted_on, df.assign(confidence=1.0), w.user, body.method[:40], "api", body.scope)
    webhooks.emit("count.saved", {"count_id": cid, "location_id": body.location_id, "lines": n, "by": w.user})
    return {"count_id": cid, "saved": n, "unknown": unknown}


@app.post("/api/v1/counts/read-images", tags=["stock counts"])
async def read_images(files: list[UploadFile] = File(...), methods: str = Form("barcode,ocr"), w: Who = Depends(need("view"))):
    """Read stock lines from photos or scans for review. Nothing is saved until POST /counts."""
    got, total = [], 0
    for f in files[:20]:
        data = await f.read()
        total += len(data)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "Images too large (60 MB in total).")
        if not vision.is_image(f.filename or ""):
            raise HTTPException(415, f"{f.filename}: send JPG, PNG, WEBP, TIFF or PDF.")
        got.append((f.filename or "image.jpg", data))
    m = tuple(x.strip() for x in methods.split(",") if x.strip() in ("barcode", "ocr", "ai"))
    key = None
    if "ai" in m:
        on = bool((db.get_setting("ai_vision", {}) or {}).get("enabled"))
        key = os.environ.get("ANTHROPIC_API_KEY") or SECRETS.get("ANTHROPIC_API_KEY")
        if not (on and key):
            m = tuple(x for x in m if x != "ai") + ("ocr",)
    df, notes = vision.extract(got, stockcount.catalog(), m or ("barcode", "ocr"), key)
    audit.log("vision.read", "api", {"files": len(got), "methods": ",".join(m), "lines": len(df), "via": w.via}, w.user, w.role)
    return {"lines": rows(vision.summarise(df)), "notes": notes}


# ----------------------------------------------------------------------------- exceptions
@app.get("/api/v1/exceptions", tags=["exceptions"])
def exceptions(status: str = "open,assigned", w: Who = Depends(need("view"))):
    x = XQ.table(tuple(s.strip() for s in status.split(",")) if status else None)
    if len(x) and w.stores:
        if w.role == "store":                     # a store sees its own problems (and chain-wide data issues), not its neighbours'
            x = x[x.location.isin(w.stores) | (x.location == "")]
        else:                                     # a regional approver sees the regions of their stores
            zones = {(XQ.locations().get(s) or {}).get("zone") for s in w.stores}
            x = x[x.location.isin(w.stores) | x.region.isin(zones) | (x.location == "")]
    return rows(x)


class ResolveIn(BaseModel):
    note: str = Field(min_length=3)


@app.post("/api/v1/exceptions/{xid}/resolve", tags=["exceptions"])
def resolve(xid: int, body: ResolveIn, w: Who = Depends(need("resolve"))):
    XQ.resolve([xid], w.user, body.note)
    audit.log("exception.resolve", str(xid), {"note": body.note[:200], "via": w.via}, w.user, w.role)
    return {"ok": True}


@app.post("/api/v1/exceptions/check-deadlines", tags=["exceptions"])
def check_deadlines(w: Who = Depends(need("view"))):
    """Raise or clear 'approvals overdue' exceptions for the latest plan (call it from a scheduler every 15-30 minutes)."""
    return {"overdue_regions": XQ.check_deadlines(user=w.user)}


# ----------------------------------------------------------------------------- plan building (upload) and jobs
@app.post("/api/v1/plans", tags=["plans"])
async def build(files: list[UploadFile] = File(...), profile: str = Form(""), w: Who = Depends(need("settings"))):
    """Upload exports (CSV / Excel / zip) and build a plan in the background. Poll GET /jobs/{job_id}."""
    tmp = Path(tempfile.mkdtemp(prefix="stockflow_api_"))
    total = 0
    for f in files[:50]:
        data = await f.read()
        total += len(data)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "Upload too large (60 MB).")
        name = Path(f.filename or "file.csv").name
        (tmp / name).write_bytes(data)
    import daily
    daily._unzip(tmp)
    prof = HERE / "brands" / f"{profile}.json" if profile and (HERE / "brands" / f"{profile}.json").exists() else None
    cfg = schema.load_config(prof, fb.load_settings())
    pack = plans.pack_dir(tmp)
    user = w.user

    def work(progress):
        import shutil
        try:
            res = pipeline.build_plan(tmp, cfg, f"API upload ({len(files)} files)", user, "upload", pack, progress, role=w.role)
            return {"plan_id": res["plan_id"]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"job_id": jobs.submit("plan", work, user, label="API upload")}


@app.get("/api/v1/jobs/{job_id}", tags=["plans"])
def job(job_id: str, w: Who = Depends(need("view"))):
    s = jobs.status(job_id)
    if not s:
        raise HTTPException(404, "No such job.")
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items() if k not in ("log", "host")}


# ----------------------------------------------------------------------------- agents (draft only; people approve)
class AgentRunIn(BaseModel):
    text: str = ""            # what-if question
    plan_id: str = ""


class DraftDecisionIn(BaseModel):
    approve: bool
    body: Optional[str] = None   # edited text, saved before approving


@app.get("/api/v1/agents", tags=["agents"])
def agent_list(w: Who = Depends(need("view"))):
    import agents as A
    import ops_agents as O
    on, key = A.ai_status(SECRETS)
    return {"ai_enabled": on, "ai_key": bool(key),
            "agents": [dict(name=k, title=t, does=d, needs=p, allowed=auth.allowed(w.role, p)) for k, (t, d, p) in O.AGENTS.items()]}


@app.post("/api/v1/agents/{name}/run", tags=["agents"])
def agent_run(name: str, body: AgentRunIn, w: Who = Depends(need("view"))):
    """Runs one agent in the background. It only writes drafts; poll GET /jobs/{job_id}, then list the drafts."""
    import ops_agents as O
    if name not in O.AGENTS or name == "onboard":
        raise HTTPException(404, "No such agent (data onboarding runs on an upload in the web app).")
    if not auth.allowed(w.role, O.AGENTS[name][2]):
        raise HTTPException(403, f"Your role ({w.role}) cannot run this agent.")
    if name == "whatif" and not body.text.strip():
        raise HTTPException(400, "Ask a what-if question in text.")
    pid = body.plan_id or None
    user = w.user
    fn = {"brief": lambda: O.run_brief(user, pid, SECRETS), "fix": lambda: O.run_fixer(user, SECRETS),
          "chase": lambda: O.run_chaser(user, SECRETS), "buy": lambda: O.run_buyer(user, pid, SECRETS),
          "whatif": lambda: O.run_whatif(body.text[:500], user, pid, SECRETS)[0]}[name]
    audit.log("agent.run", name, {"via": w.via, "text": body.text[:200]}, w.user, w.role)
    return {"job_id": jobs.submit("agent", lambda progress: {"drafts": fn()}, user, label=f"agent {name}")}


def _drafts_for(w, status, kind):
    import agents as A
    d = A.drafts(tuple(s for s in status.split(",") if s) or None, kind or None)
    if len(d) and w.stores:
        d = d[d.location.isin(w.stores)]
    return d


@app.get("/api/v1/agents/drafts", tags=["agents"])
def agent_drafts(status: str = "pending", kind: str = "", w: Who = Depends(need("view"))):
    d = _drafts_for(w, status, kind)
    if len(d) and "payload" in d:      # phone numbers stay on the server
        d = d.assign(payload=[{k: v for k, v in (p or {}).items() if k != "phone"} for p in d.payload])
    return rows(d)


@app.post("/api/v1/agents/drafts/{draft_id}", tags=["agents"])
def agent_decide(draft_id: int, body: DraftDecisionIn, w: Who = Depends(need("view"))):
    import agents as A
    d = _drafts_for(w, "pending", "")
    if not len(d) or draft_id not in set(d.id.astype(int)):
        raise HTTPException(404, "No such pending draft.")
    try:
        msg = A.decide(draft_id, w.user, w.role, body.approve, body.body, can=lambda p: auth.allowed(w.role, p), secrets=SECRETS)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "result": msg}


# WhatsApp replies from store and warehouse managers -> proposed transfer updates (drafts). Nothing is recorded until a
# person approves. Meta: set [whatsapp] verify_token and app_secret; Twilio: auth_token and inbound_url (the public URL).
def _wa():
    try:
        return dict(SECRETS["whatsapp"]) if "whatsapp" in SECRETS else {}
    except Exception:  # noqa: BLE001
        return {}


@app.get("/api/v1/whatsapp/inbound", tags=["agents"], include_in_schema=False)
def wa_verify(request: Request):
    q = request.query_params
    tok = _wa().get("verify_token") or os.environ.get("STOCKFLOW_WA_VERIFY_TOKEN")
    if tok and q.get("hub.mode") == "subscribe" and hmac.compare_digest(str(q.get("hub.verify_token", "")), str(tok)):
        return JSONResponse(int(q.get("hub.challenge", "0")) if str(q.get("hub.challenge", "")).isdigit() else q.get("hub.challenge"))
    raise HTTPException(403, "Verification failed.")


def twilio_signature(token, url, params):
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


@app.post("/api/v1/whatsapp/inbound", tags=["agents"], include_in_schema=False)
async def wa_inbound(request: Request):
    import ops_agents as O
    w = _wa()
    raw = await request.body()
    if len(raw) > 200_000:
        raise HTTPException(413, "Too large.")
    msgs = []
    sig = request.headers.get("x-hub-signature-256", "")
    if sig:                                                       # Meta WhatsApp Cloud API
        sec = w.get("app_secret") or os.environ.get("STOCKFLOW_WA_APP_SECRET")
        good = "sha256=" + hmac.new(str(sec or "").encode(), raw, hashlib.sha256).hexdigest()
        if not sec or not hmac.compare_digest(sig, good):
            raise HTTPException(403, "Bad signature.")
        j = json.loads(raw or b"{}")
        for e in j.get("entry", []):
            for c in e.get("changes", []):
                for m in (c.get("value") or {}).get("messages", []) or []:
                    if m.get("type") == "text":
                        msgs.append((m.get("from", ""), (m.get("text") or {}).get("body", "")))
    elif request.headers.get("x-twilio-signature"):              # Twilio
        tok = w.get("auth_token") or os.environ.get("STOCKFLOW_TWILIO_AUTH_TOKEN")
        form = dict((await request.form()).items())
        url = w.get("inbound_url") or str(request.url)
        if not tok or not hmac.compare_digest(request.headers["x-twilio-signature"], twilio_signature(tok, url, form)):
            raise HTTPException(403, "Bad signature.")
        msgs.append((str(form.get("From", "")).replace("whatsapp:", ""), str(form.get("Body", ""))))
    else:
        raise HTTPException(403, "Unsigned request.")
    ids = [O.handle_reply(text[:1000], phone, "whatsapp", SECRETS) for phone, text in msgs[:20] if text.strip()]
    audit.log("whatsapp.inbound", "", {"messages": len(ids)}, "whatsapp", "system")
    return {"ok": True, "drafts": ids}


# ----------------------------------------------------------------------------- webhooks and keys (admin)
class WebhookIn(BaseModel):
    url: str
    events: list[str] = []
    secret: str = ""
    active: bool = True


@app.get("/api/v1/webhooks", tags=["admin"])
def list_webhooks(w: Who = Depends(need("settings"))):
    return [dict(s, secret="set" if s.get("secret") else "") for s in webhooks.subscriptions()]


@app.put("/api/v1/webhooks", tags=["admin"])
def put_webhooks(body: list[WebhookIn], w: Who = Depends(need("settings"))):
    try:
        out = webhooks.save_subscriptions([b.model_dump() for b in body], w.user)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    audit.log("webhooks.set", "webhooks", {"count": len(out)}, w.user, w.role)
    return {"saved": len(out), "events": webhooks.EVENTS}


# ----------------------------------------------------------------------------- the phone app (PWA) and root
MOBILE = HERE.parent / "mobile" / "dist"
if MOBILE.exists():
    app.mount("/app", StaticFiles(directory=MOBILE, html=True), name="app")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/" if MOBILE.exists() else "/api/docs")


@app.get("/app", include_in_schema=False)
def app_root():
    return FileResponse(MOBILE / "index.html") if MOBILE.exists() else RedirectResponse("/api/docs")
