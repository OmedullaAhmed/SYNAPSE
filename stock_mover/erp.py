"""ERP / WMS exchange: released transfer orders out, dispatch and receipt confirmations back in.

Three channels, chosen in secrets (nothing here is ever stored in the database or shown in the app):

    [erp]
    channel = "folder"              # folder | sftp | api   (default: folder inside the app's workspace)
    format = "csv"                  # csv | json
    outbox = "/data/erp/out"        # folder: where transfer-order files are written
    inbox = "/data/erp/in"          # folder: where the WMS drops dispatch / receipt files
    # sftp
    host = "sftp.partner.com"  port = 22  username = "stockflow"  password = "…"   (or private_key = "-----BEGIN …")
    remote_outbox = "/outbound"  remote_inbox = "/inbound"
    # api
    orders_url = "https://erp.example.com/api/transfer-orders"      # POST, one JSON document per release
    events_url = "https://erp.example.com/api/stock-movements"      # GET ?since=ISO-time -> [{to_number, sku_id, event, qty, at}]
    token = "…"   auth_header = "Authorization"   auth_prefix = "Bearer "
    # field names the ERP expects (optional): standard name -> ERP name
    [erp.fields]
    to_number = "DocumentNo"  from_location = "FromSite"  to_location = "ToSite"  sku_id = "ItemCode"  qty = "Quantity"

Inbound files (csv, xlsx or json) need to_number, sku_id and a quantity (qty / qty_shipped / qty_received). The event is
taken from an 'event' column, else from the file name (dispatch / asn / shipment -> dispatched; receipt / grn -> received).
Every file is processed once (by checksum) and logged in erp_exchange.
"""
import hashlib
import io
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

import db

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE / "workspace" / "erp"
STD_FIELDS = ["to_number", "line_no", "from_location", "to_location", "sku_id", "qty", "ship_date", "expected_receipt",
              "move_type", "reason_group", "move_id"]
DISPATCH_WORDS, RECEIPT_WORDS = ("dispatch", "asn", "shipment", "shipped", "despatch", "outward"), ("receipt", "grn", "received", "inward")


def conf(secrets=None):
    c = {}
    try:
        if secrets is not None and "erp" in secrets:  # may raise when there is no secrets file
            c = {k: (dict(v) if hasattr(v, "keys") else v) for k, v in dict(secrets["erp"]).items()}
    except Exception:  # noqa: BLE001
        c = {}
    for k in ("channel", "format", "outbox", "inbox", "orders_url", "events_url", "token", "host", "username", "password"):
        e = os.environ.get(f"STOCKFLOW_ERP_{k.upper()}")
        if e and k not in c:
            c[k] = e
    c.setdefault("channel", "folder")
    c.setdefault("format", "csv")
    c.setdefault("outbox", str(DEFAULT_DIR / "out"))
    c.setdefault("inbox", str(DEFAULT_DIR / "in"))
    return c


def describe(c):
    ch = c["channel"]
    if ch == "api":
        return f"API → {urllib.parse.urlparse(c.get('orders_url', '')).netloc or 'not set'}"
    if ch == "sftp":
        return f"SFTP {c.get('host', 'not set')}:{c.get('remote_outbox', '/')}"
    return f"Folder {c['outbox']}"


def _log(direction, channel, target, kind, file, records, status, error="", by="", checksum=""):
    try:
        db.insert_rows("erp_exchange", [dict(at=datetime.now(), direction=direction, channel=channel, target=str(target)[:300], kind=kind,
                                             file=str(file)[:300], records=int(records), status=status, error=str(error)[:2000], by=by,
                                             checksum=checksum)])
    except Exception:  # noqa: BLE001
        pass


def _done_checksums():
    try:
        d = db.read("erp_exchange")
        return set(d[(d.direction == "in") & (d.status == "ok")].checksum.dropna()) if len(d) else set()
    except Exception:  # noqa: BLE001
        return set()


def render(orders, c, plan_id=""):
    """(file name, bytes) for a set of transfer-order lines in the ERP's field names and format."""
    o = orders[[f for f in STD_FIELDS if f in orders]].copy()
    o.insert(0, "plan_id", plan_id)
    o = o.rename(columns=c.get("fields") or {})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if c.get("format") == "json":
        doc = {"source": "StockFlow", "plan_id": plan_id, "created_at": datetime.now().isoformat(timespec="seconds"),
               "orders": [{"number": n, "lines": g.drop(columns=["plan_id"]).to_dict("records")}
                          for n, g in o.groupby(o.columns[1], sort=False)]}
        return f"transfer_orders_{stamp}.json", json.dumps(doc, default=str, indent=1).encode()
    import safety
    return f"transfer_orders_{stamp}.csv", safety.csv(o)


def _sftp(c):
    import paramiko   # optional dependency, only for the sftp channel
    t = paramiko.Transport((c["host"], int(c.get("port", 22))))
    if c.get("private_key"):
        key = paramiko.RSAKey.from_private_key(io.StringIO(c["private_key"]))
        t.connect(username=c["username"], pkey=key)
    else:
        t.connect(username=c["username"], password=c.get("password"))
    return t, paramiko.SFTPClient.from_transport(t)


def _http(url, c, data=None, method="GET"):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if c.get("token"):
        req.add_header(c.get("auth_header", "Authorization"), c.get("auth_prefix", "Bearer ") + c["token"])
    with urllib.request.urlopen(req, timeout=30) as r:   # noqa: S310 - URL comes from the admin's secrets
        return r.status, r.read()


def export_orders(orders, plan_id, user, secrets=None):
    """Send released transfer orders to the ERP / WMS. Returns (ok, message). Never raises."""
    c = conf(secrets)
    if orders is None or len(orders) == 0:
        return False, "Nothing to send."
    name, body = render(orders, c, plan_id)
    chk = hashlib.sha256(body).hexdigest()
    n = int(orders.to_number.nunique())
    try:
        if c["channel"] == "api":
            if not c.get("orders_url"):
                raise ValueError("erp.orders_url is not set")
            doc = json.loads(render(orders, {**c, "format": "json"}, plan_id)[1])
            code, _ = _http(c["orders_url"], c, json.dumps(doc).encode(), "POST")
            target = c["orders_url"]
            if code >= 300:
                raise RuntimeError(f"HTTP {code}")
        elif c["channel"] == "sftp":
            t, s = _sftp(c)
            try:
                target = f"{c.get('remote_outbox', '.').rstrip('/')}/{name}"
                with s.open(target + ".part", "wb") as f:
                    f.write(body)
                s.rename(target + ".part", target)       # partner never reads a half-written file
            finally:
                t.close()
        else:
            out = Path(c["outbox"])
            out.mkdir(parents=True, exist_ok=True)
            (out / (name + ".part")).write_bytes(body)
            (out / (name + ".part")).replace(out / name)
            target = out / name
        _log("out", c["channel"], target, "transfer_orders", name, len(orders), "ok", by=user, checksum=chk)
        return True, f"Sent {n} transfer orders ({len(orders)} lines) to {describe(c)}."
    except Exception as e:  # noqa: BLE001
        _log("out", c["channel"], c.get("orders_url") or c.get("host") or c["outbox"], "transfer_orders", name, len(orders), "failed",
             f"{type(e).__name__}: {e}", user, chk)
        return False, f"Could not send to the ERP ({type(e).__name__}: {e}). The orders are released in StockFlow; retry from Operations."


def _event_of(name, df):
    if "event" in df.columns:
        return None
    n = name.lower()
    if any(w in n for w in DISPATCH_WORDS):
        return "dispatched"
    if any(w in n for w in RECEIPT_WORDS):
        return "received"
    cols = set(df.columns)
    return "received" if "qty_received" in cols else "dispatched" if "qty_shipped" in cols else None


def _frame(name, data):
    if name.lower().endswith(".json"):
        j = json.loads(data)
        return pd.json_normalize(j.get("events", j) if isinstance(j, dict) else j)
    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data), dtype=str)
    return pd.read_csv(io.BytesIO(data), dtype=str)


def apply_events(name, df, user, fields=None):
    """Turn one inbound file into dispatch / receipt events. Returns (dispatched rows, received rows)."""
    import feedback as fb
    inv = {v.lower(): k for k, v in (fields or {}).items()}
    df.columns = [inv.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]
    ev = _event_of(name, df)
    if ev is None and "event" not in df.columns:
        raise ValueError("can't tell dispatches from receipts: add an 'event' column or put dispatch / receipt in the file name")
    if ev:
        df["event"] = ev
    df["event"] = df.event.astype(str).str.lower().map(
        lambda e: "dispatched" if any(w in e for w in DISPATCH_WORDS) else "received" if any(w in e for w in RECEIPT_WORDS) else e)
    nd = nr = 0
    d = df[df.event == "dispatched"]
    if len(d):
        nd = fb.add_dispatches(d.drop(columns="event"), user)
    r = df[df.event == "received"]
    if len(r):
        nr = fb.add_receipts(r.drop(columns="event"), user)
    return nd, nr


def poll(user="erp", secrets=None):
    """Pick up new dispatch / receipt confirmations from the inbox (folder, SFTP or API). Returns a summary dict."""
    c = conf(secrets)
    seen = _done_checksums()
    got = []                                   # (name, bytes, move_done callback)
    try:
        if c["channel"] == "api":
            if c.get("events_url"):
                last = db.get_setting("erp_last_pull", "")
                url = c["events_url"] + (("&" if "?" in c["events_url"] else "?") + "since=" + urllib.parse.quote(last) if last else "")
                _, body = _http(url, c)
                got.append((f"api_{datetime.now():%Y%m%d_%H%M%S}.json", body, lambda: db.upsert_setting(
                    "erp_last_pull", datetime.now().isoformat(timespec="seconds"), user)))
        elif c["channel"] == "sftp":
            t, s = _sftp(c)
            try:
                rin = c.get("remote_inbox", ".").rstrip("/")
                for f in s.listdir(rin):
                    if f.lower().endswith((".csv", ".json", ".xlsx")):
                        with s.open(f"{rin}/{f}", "rb") as fh:
                            got.append((f, fh.read(), None))
            finally:
                t.close()
        else:
            inbox = Path(c["inbox"])
            inbox.mkdir(parents=True, exist_ok=True)
            for f in sorted(inbox.iterdir()):
                if f.is_file() and f.suffix.lower() in (".csv", ".json", ".xlsx"):
                    got.append((f.name, f.read_bytes(), (lambda p=f: _archive(p))))
    except Exception as e:  # noqa: BLE001
        _log("in", c["channel"], c.get("events_url") or c.get("host") or c["inbox"], "events", "", 0, "failed", f"{type(e).__name__}: {e}", user)
        return dict(ok=False, files=0, dispatched=0, received=0, skipped=0, error=f"{type(e).__name__}: {e}")
    res = dict(ok=True, files=0, dispatched=0, received=0, skipped=0, errors=[])
    for name, body, done in got:
        chk = hashlib.sha256(body).hexdigest()
        if chk in seen:
            res["skipped"] += 1
            if done and c["channel"] == "folder":
                done()
            continue
        try:
            nd, nr = apply_events(name, _frame(name, body), user, c.get("fields"))
            res["files"] += 1
            res["dispatched"] += nd
            res["received"] += nr
            _log("in", c["channel"], c.get("inbox", ""), "events", name, nd + nr, "ok", by=user, checksum=chk)
            if done:
                done()
        except Exception as e:  # noqa: BLE001
            res["errors"].append(f"{name}: {e}")
            _log("in", c["channel"], c.get("inbox", ""), "events", name, 0, "failed", f"{type(e).__name__}: {e}", user, chk)
    return res


def _archive(p):
    dest = p.parent / "processed"
    dest.mkdir(exist_ok=True)
    p.replace(dest / f"{datetime.now():%Y%m%d%H%M%S}_{p.name}")


def log(limit=200):
    try:
        return db.read("erp_exchange", order=["-at"], limit=limit)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
