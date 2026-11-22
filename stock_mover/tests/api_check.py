"""REST API checks (FastAPI TestClient, real database):  python tests/api_check.py   (DATABASE_URL optional)."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/api_check.db"
os.environ.setdefault("STOCKFLOW_API_SECRET", "test-secret-not-for-production")

from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
import auth  # noqa: E402
import pipeline  # noqa: E402
import schema  # noqa: E402

C = TestClient(api.app)
OK = []
GOT = []


def check(name, cond, ev=""):
    OK.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name} — {str(ev)[:150]}", flush=True)


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        GOT.append((self.headers.get("X-StockFlow-Event"), self.headers.get("X-StockFlow-Signature"), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def tok(u, p="Strongpass123"):
    r = C.post("/api/v1/auth/token", json={"username": u, "password": p})
    return {"Authorization": f"Bearer {r.json()['access_token']}"} if r.status_code == 200 else None


def main():
    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for u, r, s in (("admin1", "admin", ""), ("appr1", "approver", ""), ("view1", "viewer", ""), ("kol02", "store", "KOL-02")):
        auth.create_user(u, "Strongpass123", r, s, "test", must_change=False)
    res = pipeline.build_plan(HERE / "data" / "demo_general", schema.load_config(HERE / "brands" / "demo_general.json"), "api test", "admin1", "upload")
    pid = res["plan_id"]

    check("health is public and reports the database", C.get("/api/v1/health").json()["status"] == "ok", C.get("/api/v1/health").json())
    check("no token -> 401", C.get("/api/v1/plans").status_code == 401)
    check("wrong password -> 401", C.post("/api/v1/auth/token", json={"username": "view1", "password": "nope"}).status_code == 401)
    forged = {"Authorization": "Bearer " + api.make_token("admin1", "admin")[:-4] + "AAAA"}
    check("tampered token -> 401", C.get("/api/v1/me", headers=forged).status_code == 401)
    A, P, V, S = tok("admin1"), tok("appr1"), tok("view1"), tok("kol02")
    adm = C.put("/api/v1/webhooks", headers=A, json=[{"url": f"http://127.0.0.1:{srv.server_port}/hook", "secret": "whsec", "events": []}])
    check("admin sets a webhook", adm.status_code == 200, adm.json())
    check("viewer cannot set webhooks (403)", C.put("/api/v1/webhooks", headers=V, json=[]).status_code == 403)

    p = C.get(f"/api/v1/plans/{pid}", headers=P).json()
    trips = p["trips"]
    check("plan with trips by region", len(trips) > 0 and len(p["regions"]) >= 2, f"{len(trips)} trips, regions {[r['region'] for r in p['regions']]}")
    sp = C.get(f"/api/v1/plans/{pid}", headers=S).json()["trips"]
    check("store user sees only trips touching KOL-02", sp and all("KOL-02" in (t["from_id"], t["to_id"]) for t in sp), f"{len(sp)} trips")
    t0 = trips[0]["to_number"]
    check("viewer cannot approve (403)", C.post(f"/api/v1/plans/{pid}/trips/{t0}/decision", headers=V, json={"decision": "approved"}).status_code == 403)
    lines = C.get(f"/api/v1/plans/{pid}/trips/{t0}", headers=P).json()
    r = C.post(f"/api/v1/plans/{pid}/lines", headers=P, json=[{"move_id": lines[0]["move_id"], "decision": "approved", "approved_qty": 1}])
    check("approver reduces a line", r.json().get("changed") == 1, r.json())
    for t in trips[:3]:
        C.post(f"/api/v1/plans/{pid}/trips/{t['to_number']}/decision", headers=P, json={"decision": "approved"})
    C.post(f"/api/v1/plans/{pid}/trips/{trips[3]['to_number']}/decision", headers=P, json={"decision": "rejected"})
    rel = C.post(f"/api/v1/plans/{pid}/release", headers=P, json={})
    check("release sends exactly the 3 approved trips", rel.status_code == 200 and len(rel.json()["released"]) == 3, rel.json())
    check("releasing again finds nothing (409)", C.post(f"/api/v1/plans/{pid}/release", headers=P, json={}).status_code == 409)
    tr = C.get("/api/v1/transfers", headers=V).json()
    check("released trips are tracked", len(tr) == 3, [x["status"] for x in tr])
    one = tr[0]
    d = C.post(f"/api/v1/transfers/{one['to_number']}/dispatch", headers=V, json={})
    check("viewer cannot mark dispatch (403)", d.status_code == 403)
    d = C.post(f"/api/v1/transfers/{one['to_number']}/dispatch", headers=P, json={})
    det = C.get(f"/api/v1/transfers/{one['to_number']}", headers=P).json()
    first = det["lines"][0]
    rc = C.post(f"/api/v1/transfers/{one['to_number']}/receive", headers=P, json={"lines": {first["sku_id"]: max(0, first["sent"] - 1)}})
    st = C.get(f"/api/v1/transfers/{one['to_number']}", headers=P).json()["order"]["status"]
    check("dispatch then short receipt -> 'Received short'", d.status_code == 200 and rc.status_code == 200 and st == "Received short", st)
    x = C.get("/api/v1/exceptions", headers=P).json()
    hit = [e for e in x if e["ref"] == one["to_number"]]
    check("short receipt opens an exception", len(hit) == 1, hit[0]["title"] if hit else x[:1])
    rs = C.post(f"/api/v1/exceptions/{hit[0]['id']}/resolve", headers=P, json={"note": "Driver returned the missing unit"})
    check("approver resolves it", rs.status_code == 200)

    cat = C.get("/api/v1/catalog?q=power", headers=V).json()
    bc = cat[0]["barcode"]
    cnt = C.post("/api/v1/counts", headers=S, json={"location_id": "KOL-02", "counted_on": "2026-10-31", "lines": [
        {"barcode": bc, "qty": 7}, {"sku_id": cat[1]["sku_id"], "qty": 3}, {"barcode": "0000000000000", "qty": 1}]})
    check("store counts its own location by barcode", cnt.status_code == 200 and cnt.json()["saved"] == 2 and cnt.json()["unknown"] == ["0000000000000"], cnt.json())
    other = C.post("/api/v1/counts", headers=S, json={"location_id": "DEL-01", "counted_on": "2026-10-31", "lines": [{"sku_id": "MOB001", "qty": 1}]})
    check("store cannot count another location (403)", other.status_code == 403)
    img = open("/tmp/vis/count_sheet.png", "rb").read() if Path("/tmp/vis/count_sheet.png").exists() else None
    if img:
        ri = C.post("/api/v1/counts/read-images", headers=S, files=[("files", ("sheet.png", img, "image/png"))], data={"methods": "ocr"})
        check("read stock lines from a photo of a count sheet", ri.status_code == 200 and len(ri.json()["lines"]) >= 15, f"{len(ri.json().get('lines', []))} lines")
    bad = C.post("/api/v1/counts/read-images", headers=S, files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    check("non-image upload refused (415)", bad.status_code == 415)

    key = api.make_api_key("WMS robot", "approver", "", "admin1")
    K = {"Authorization": f"Bearer {key}"}
    check("API key works for machine access", C.get("/api/v1/me", headers=K).json().get("via") == "key")
    check("revoked/forged API key -> 401", C.get("/api/v1/me", headers={"Authorization": "Bearer " + key[:-3] + "xyz"}).status_code == 401)

    import time
    time.sleep(3)
    events = [e for e, _, _ in GOT]
    good_sig = all(s == "sha256=" + hmac.new(b"whsec", b, hashlib.sha256).hexdigest() for _, s, b in GOT)
    check("webhooks delivered and signed (HMAC-SHA256)", {"trips.released", "transfer.dispatched", "count.saved"} <= set(events) and good_sig,
          sorted(set(events)))
    import audit
    acts = set(audit.as_frame(5000).action)
    check("API actions are audited", {"queue.decide", "orders.release", "move.dispatched", "count.save", "exception.resolve", "apikey.create"} <= acts,
          sorted(acts)[:12])
    h = C.get("/api/v1/health")
    check("security headers set", h.headers.get("x-content-type-options") == "nosniff" and h.headers.get("x-frame-options") == "DENY")
    print(f"\n{sum(OK)}/{len(OK)} API checks passed")
    srv.shutdown()
    return 0 if all(OK) else 1


if __name__ == "__main__":
    sys.exit(main())
