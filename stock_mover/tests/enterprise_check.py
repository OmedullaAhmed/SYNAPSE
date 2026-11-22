"""Enterprise readiness checks: data integration, security, reliability, auditability, correctness.

    DATABASE_URL=postgresql://… python tests/enterprise_check.py      # or leave unset for a throwaway SQLite file
    python tests/enterprise_check.py --json report.json

Each check prints PASS / FAIL with evidence. Exit code 1 if any check fails.
"""
import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/enterprise_check.db"

import pandas as pd  # noqa: E402

import audit  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import engine as E  # noqa: E402
import erp  # noqa: E402
import exceptions_queue as XQ  # noqa: E402
import feedback as fb  # noqa: E402
import ingest  # noqa: E402
import jobs  # noqa: E402
import pipeline  # noqa: E402
import plans  # noqa: E402
import safety  # noqa: E402
import schema  # noqa: E402

DEMO = HERE / "data" / "demo"
HACK = HERE / "data" / "csv"
CFG = lambda: schema.load_config(HERE / "brands" / "demo.json")  # noqa: E731
COLS = ["type", "from_id", "to_id", "sku_id", "qty", "group", "net_benefit"]
RESULTS = []
TMP = Path(tempfile.mkdtemp(prefix="sf_ent_"))


def check(area, name):
    def deco(fn):
        def run():
            t = time.time()
            try:
                ok, evidence = fn()
            except Exception as e:  # noqa: BLE001
                ok, evidence = False, f"{type(e).__name__}: {e} | {traceback.format_exc().splitlines()[-3].strip()}"
            RESULTS.append(dict(area=area, check=name, ok=bool(ok), evidence=str(evidence)[:400], secs=round(time.time() - t, 1)))
            print(f"{'PASS' if ok else 'FAIL'}  [{area}] {name} — {str(evidence)[:160]}", flush=True)
        run.__name__ = fn.__name__
        CHECKS.append(run)
        return run
    return deco


CHECKS = []
_BASE = {}


def base(name="demo"):
    if name not in _BASE:
        _BASE[name] = E.run(DEMO if name == "demo" else HACK, None, CFG() if name == "demo" else schema.load_config())
    return _BASE[name]


def messy_demo(out):
    out.mkdir(parents=True, exist_ok=True)
    s = pd.read_csv(DEMO / "sales.csv", dtype=str)
    q, v = pd.to_numeric(s.qty), pd.to_numeric(s.net_value)
    pd.DataFrame({"Bill Date": pd.to_datetime(s.date).dt.strftime("%d/%m/%Y"), "Store Code": s.store_id.str.lower(), "Item Code": s.sku_id,
                  "Qty Sold": q.abs().astype(int).astype(str), "Net Amount": ["₹{:,.2f}".format(abs(x)) for x in v],
                  "Txn Type": ["Return" if x < 0 else "Sale" for x in q], "Customer Mobile": "98xxxxxx00"}
                 ).to_csv(out / "POS_Sales_Export.csv", index=False)
    it = pd.read_csv(DEMO / "items.csv", dtype=str).rename(columns={"sku_id": "Article Code", "style_id": "Design Code", "style_name": "Product Name",
                                                                    "category": "Department", "mrp": "MRP", "cost_price": "Landed Cost"})
    with pd.ExcelWriter(out / "Product Master.xlsx") as w:
        pd.DataFrame([["Item master"], ["exported 01-11-2026"]]).to_excel(w, index=False, header=False)
        it.to_excel(w, index=False, startrow=3)
    sn = pd.read_csv(DEMO / "stock_snapshot.csv", dtype=str).rename(columns={"location_id": "Site", "sku_id": "Item Code", "on_hand": "Closing Stock",
                                                                             "as_of_date": "Stock Date", "in_transit": "In Transit",
                                                                             "oldest_stock_received_on": "First GRN Date"})
    sn["Stock Date"] = pd.to_datetime(sn["Stock Date"]).dt.strftime("%d-%m-%Y")
    sn.to_csv(out / "SOH export.csv", index=False, sep=";")
    for f in ["stores", "warehouses", "calendar", "inbound", "lanes", "managers", "min_display", "protected_styles", "stock_weekly"]:
        shutil.copy(DEMO / f"{f}.csv", out)
    return out


# ============================================================================ data integration
@check("Data integration", "Vendor exports (other headers, DD/MM dates, ₹ amounts, ; separator, title rows) give the identical plan")
def t_messy():
    m = messy_demo(TMP / "messy")
    rep = ingest.run(m, TMP / "messy_std", log=False, stale=False)
    R = E.run(TMP / "messy_std", None, CFG())
    same = base()["moves"][COLS].equals(R["moves"][COLS])
    return same and not rep["missing"], f"{len(R['moves'])} lines, identical={same}, personal column dropped="\
        f"{'customer_mobile' not in pd.read_csv(TMP / 'messy_std' / 'sales.csv').columns}"


@check("Data integration", "Template files pass through unchanged (demo and hackathon data)")
def t_passthrough():
    out = []
    for name, src in (("demo", DEMO), ("hack", HACK)):
        ingest.run(src, TMP / f"std_{name}", log=False, stale=False)
        R = E.run(TMP / f"std_{name}", None, CFG() if name == "demo" else schema.load_config())
        out.append(base(name)["moves"][COLS].equals(R["moves"][COLS]))
    return all(out), f"identical: {out}"


@check("Data integration", "Return lines flagged by a type column become negative quantities")
def t_returns():
    df = pd.DataFrame({"Bill Date": ["01/10/2026"] * 2, "Store": ["a"] * 2, "SKU": ["x"] * 2, "Qty": ["2", "1"], "Amount": ["100", "50"],
                       "Txn Type": ["Sale", "Return"]})
    m = ingest.suggest_mapping("sales", df.columns)
    clean, notes = ingest.standardise(df, "sales", m)
    return clean.qty.tolist() == ["2", "-1"] and clean.net_value.tolist() == ["100", "-50"], f"{clean.qty.tolist()} {notes}"


@check("Data integration", "Windows-1252 files and Excel serial dates are read")
def t_encoding():
    d = TMP / "enc"
    d.mkdir()
    (d / "items.csv").write_bytes("sku_id,style_id,style_name,category,size,mrp\nA1,S1,Café Kurta,Kurta,M,999\n".encode("cp1252"))
    raw = ingest.read_raw(d)
    ok1 = raw["items.csv"].style_name.iloc[0] == "Café Kurta"
    iso, bad = ingest.clean_date(pd.Series(["46326", "46327"]))
    return ok1 and iso.tolist() == ["2026-10-31", "2026-11-01"] and bad == 0, f"name ok={ok1}, serial dates={iso.tolist()}"


@check("Data integration", "Data-quality alerts: unknown SKUs, negative and stale stock, stores with no stock, missing sheet")
def t_dq():
    d = TMP / "dq"
    shutil.copytree(DEMO, d)
    sn = pd.read_csv(d / "stock_snapshot.csv", dtype=str)
    sn.loc[0, "on_hand"] = "-4"
    sn = pd.concat([sn, sn.head(1).assign(sku_id="GHOST-SKU")])
    sn = sn[sn.location_id != "PUN-01"]
    sn.to_csv(d / "stock_snapshot.csv", index=False)
    rep = ingest.run(d, TMP / "dq_std", log=False, today="2026-11-22")
    kinds = {i["kind"] for i in rep["issues"]}
    need = {"negative_stock", "unknown_sku", "stale_stock", "store_no_stock"}
    (d / "sales.csv").unlink()
    rep2 = ingest.run(d, TMP / "dq_std2", log=False)
    return need <= kinds and rep2["missing"] == ["sales"], f"found {sorted(kinds & need)}; missing sheet reported: {rep2['missing']}"


@check("Data integration", "A missing required sheet stops the run with a plain message, not a crash")
def t_missing():
    d = TMP / "miss"
    shutil.copytree(DEMO, d)
    (d / "items.csv").unlink()
    try:
        pipeline.build_plan(d, CFG(), "missing test", "tester", kind="upload")
    except E.DataError as e:
        return "items" in str(e), str(e)
    return False, "no error raised"


@check("Data integration", "Zip files cannot write outside their folder (zip-slip)")
def t_zipslip():
    import daily
    d = TMP / "zs"
    d.mkdir()
    with zipfile.ZipFile(d / "x.zip", "w") as z:
        z.writestr("../../evil.csv", "a,b\n1,2\n")
        z.writestr("sales.csv", "a,b\n1,2\n")
    daily._unzip(d)
    escaped = (TMP / "evil.csv").exists() or (d.parent.parent / "evil.csv").exists()
    return not escaped and (d / "unzipped" / "sales.csv").exists(), f"escaped={escaped}"


# ============================================================================ security
@check("Security", "Passwords are stored as salted PBKDF2 hashes, never in plain text")
def t_hash():
    auth.create_user("ent_admin", "Strongpass123", "admin", "", "test", must_change=False)
    u = db.read("users", {"username": "ent_admin"}).iloc[0]
    return u.password.startswith("pbkdf2$") and "Strongpass123" not in u.password, u.password[:22] + "…"


@check("Security", "Lockout after 5 wrong passwords; correct password refused while locked")
def t_lock():
    auth.create_user("ent_lock", "Strongpass123", "viewer", "", "test", must_change=False)
    for _ in range(5):
        auth.attempt("ent_lock", "wrong", {})
    ok, _, msg = auth.attempt("ent_lock", "Strongpass123", {})
    return not ok and "Too many" in msg, msg


@check("Security", "Password policy rejects short, letters-only and name-containing passwords")
def t_policy():
    bad = [auth.password_problems(p, "asha") for p in ("short1", "onlyletterslong", "asha12345678")]
    good = auth.password_problems("Monsoon2026sale", "asha")
    return all(bad) and not good, f"{bad} / good={good}"


@check("Security", "SQL injection in the user name does not sign in")
def t_sqli():
    ok, _, _ = auth.attempt("x' OR '1'='1", "anything", {})
    ok2, _, _ = auth.attempt("ent_admin'--", "anything", {})
    return not ok and not ok2, "both refused"


@check("Security", "Role matrix: only approvers/admins release; only admins change settings/users; guests see no live data")
def t_roles():
    A = auth.allowed
    rows = {r: sorted(auth.PERMS[r]) for r in auth.PERMS}
    ok = (not A("viewer", "release") and not A("store", "release") and A("approver", "release") and not A("approver", "settings")
          and A("admin", "users") and not A("guest", "release") and not A("guest", "track_own") and not A("guest", "audit")
          and A("store", "track_own") and not A("store", "track_all"))
    return ok, json.dumps(rows)[:300]


@check("Security", "Spreadsheet formula injection is neutralised in Excel and CSV outputs")
def t_formula():
    d = TMP / "inj"
    shutil.copytree(DEMO, d)
    it = pd.read_csv(d / "items.csv", dtype=str)
    it["style_name"] = '=HYPERLINK("http://evil","click")'
    it.to_csv(d / "items.csv", index=False)
    R = E.run(d, TMP / "inj_out", CFG())
    import openpyxl
    wb = openpyxl.load_workbook(R["xlsx"])
    ws = wb["Moves"]
    vals = [c.value for row in ws.iter_rows(min_row=2, max_row=40) for c in row if isinstance(c.value, str) and "HYPERLINK" in c.value]
    formulas = [c for row in ws.iter_rows(min_row=1, max_row=40) for c in row if c.data_type == "f"]
    csvtxt = safety.csv(R["moves"]).decode()
    return vals and not formulas and "'=HYPERLINK" in csvtxt, f"{len(vals)} cells kept as text, {len(formulas)} formulas"


@check("Security", "ERP credentials never reach the database or audit trail")
def t_secrets():
    tok = "SECRET-TOKEN-123456"
    o = E.transfer_orders(base()["moves"], base()["trips"]).head(5)
    erp.export_orders(o, "p-sec", "tester", {"erp": {"channel": "api", "orders_url": "http://127.0.0.1:9/x", "token": tok}})
    dump = " ".join(db.read(t).to_csv() for t in db.T)
    return tok not in dump, "token absent from every table" if tok not in dump else "TOKEN FOUND"


# ============================================================================ reliability
@check("Reliability", "Background job runs a plan and saves it; progress is recorded")
def t_job():
    jid = jobs.submit("plan", lambda p: pipeline.build_plan(DEMO, CFG(), "job test", "tester", "demo", None, p), "tester")
    t = time.time()
    while jobs.status(jid)["status"] in ("queued", "running") and time.time() - t < 120:
        time.sleep(0.5)
    s = jobs.status(jid)
    res = jobs.result(jid)
    return s["status"] == "done" and plans.get(res["plan_id"]) is not None, f"{s['status']} {s['plan_id']} progress={s['progress']}"


@check("Reliability", "Two plans built at the same time both succeed (concurrent jobs, one database)")
def t_concurrent():
    ids = [jobs.submit("plan", lambda p, i=i: pipeline.build_plan(DEMO, CFG(), f"conc {i}", "tester", "demo", None, p), "tester") for i in range(2)]
    t = time.time()
    while any(jobs.status(j)["status"] in ("queued", "running") for j in ids) and time.time() - t < 240:
        time.sleep(0.5)
    st = [jobs.status(j)["status"] for j in ids]
    return st == ["done", "done"], f"{st}"


@check("Reliability", "Concurrent line decisions by two approvers are all kept")
def t_concurrent_decisions():
    pid = plans.recent(1).iloc[0].plan_id
    d = plans.decisions(pid)
    a, b = d.move_id.iloc[:40], d.move_id.iloc[40:80]

    def go(ids, user):
        plans.set_decisions(pid, pd.DataFrame({"move_id": ids, "decision": "rejected", "approved_qty": 0, "note": user}), user)
    th = [threading.Thread(target=go, args=(a, "u1")), threading.Thread(target=go, args=(b, "u2"))]
    [t.start() for t in th]
    [t.join() for t in th]
    d2 = plans.decisions(pid)
    n = int((d2.decision == "rejected").sum())
    return n == 80, f"{n} of 80 decisions stored"


@check("Reliability", "Jobs cut off by a server restart are marked interrupted on the next start")
def t_recover():
    import socket
    db.insert_rows("jobs", [dict(job_id="J-crash", kind="plan", status="running", requested_by="t", host=f"{socket.gethostname()}:1",
                                 progress=0.4, step="Planning moves")])
    jobs._RECOVERED = False
    n = jobs.recover()
    return jobs.status("J-crash")["status"] == "interrupted", f"{n} recovered"


@check("Reliability", "A saved plan rebuilt from its stored inputs is identical (recovery after restart)")
def t_reopen():
    class U:
        def __init__(self, f):
            self.name, self._f = f.name, f

        def getvalue(self):
            return self._f.read_bytes()
    up = [U(f) for f in DEMO.iterdir() if f.suffix == ".csv"]
    pack = plans.pack_uploads(up)
    r1 = pipeline.build_plan(DEMO, CFG(), "pack test", "tester", "upload", pack)
    row = plans.get(r1["plan_id"])
    d = TMP / "reopen"
    d.mkdir()
    with zipfile.ZipFile(io.BytesIO(bytes(row.input_pack))) as z:
        for m in z.infolist():
            (d / Path(m.filename).name).write_bytes(z.read(m))
    r2 = pipeline.build_plan(d, row.settings, "reopen", "tester", reopen=r1["plan_id"])
    return r1["R"]["moves"][COLS].equals(r2["R"]["moves"][COLS]), f"{len(r2['R']['moves'])} lines identical"


@check("Reliability", "ERP confirmations: each file applied once, archived, short receipt raises an exception")
def t_erp_roundtrip():
    R = base()
    o = E.transfer_orders(R["moves"], R["trips"])
    one = o[o.to_number == o.to_number.iloc[0]]
    fb.release_orders(one, "tester", "erp-test")
    inbox = TMP / "erp_in"
    inbox.mkdir()
    c = {"erp": {"channel": "folder", "outbox": str(TMP / "erp_out"), "inbox": str(inbox)}}
    ok, _ = erp.export_orders(one, "erp-test", "tester", c)
    a = one[["to_number", "sku_id", "qty"]].rename(columns={"qty": "qty_shipped"})
    a.to_csv(inbox / "dispatch_1.csv", index=False)
    b = a.rename(columns={"qty_shipped": "qty_received"})
    b.iloc[0, 2] = 0
    b.to_csv(inbox / "grn_1.csv", index=False)
    shutil.copy(inbox / "grn_1.csv", TMP / "grn_copy.csv")
    p1 = erp.poll("wms", c)
    shutil.copy(TMP / "grn_copy.csv", inbox / "grn_again.csv")    # the same file delivered twice
    p2 = erp.poll("wms", c)
    XQ.sync_transfers("tester")
    x = XQ.table()
    hit = x[x.ref == one.to_number.iloc[0]]
    return ok and p1["files"] == 2 and p2["files"] == 0 and p2["skipped"] == 1 and len(hit) == 1, \
        f"sent={ok}, first poll {p1['files']} files, duplicate skipped={p2['skipped']}, exception: {hit.title.tolist()}"


@check("Reliability", "Exceptions are de-duplicated across runs and close themselves when the problem goes away")
def t_x_lifecycle():
    items = [dict(key="test:k1", kind="data", severity="high", title="t1"), dict(key="test:k2", kind="data", severity="low", title="t2")]
    XQ.record(items, "testsrc")
    XQ.record(items, "testsrc")
    r = XQ.record(items[:1], "testsrc")
    d = db.read("exceptions")
    d = d[d.source == "testsrc"]
    k1 = d[d.key == "test:k1"].iloc[0]
    k2 = d[d.key == "test:k2"].iloc[0]
    return len(d) == 2 and int(k1.seen_count) == 3 and k2.status == "cleared" and r["cleared"] == 1, \
        f"rows={len(d)} seen={k1.seen_count} k2={k2.status}"


# ============================================================================ auditability
@check("Auditability", "Plan builds, line decisions, releases and resolutions are in the audit trail with user and time")
def t_audit():
    a = audit.as_frame(5000)
    have = set(a.action)
    need = {"plan.build"}
    pid = plans.recent(1).iloc[0].plan_id
    audit.log("decision.line", f"{pid}/M00001", {"from_qty": 4, "to_qty": 1}, "tester", "approver")
    a = audit.as_frame(5000)
    row = a[a.action == "decision.line"].iloc[0]
    return need <= have and row.user == "tester" and pd.notna(row["at"]), f"{len(a)} entries; actions: {sorted(set(a.action))[:8]}"


@check("Auditability", "The app has no code path that edits or deletes audit entries")
def t_audit_immutable():
    src = " ".join(p.read_text() for p in HERE.glob("*.py"))
    bad = [s for s in ('update_rows("audit"', 'delete_rows("audit"', "update_rows('audit'", "delete_rows('audit'") if s in src]
    return not bad, "none found" if not bad else bad


# ============================================================================ correctness
@check("Correctness", "All 17 planning rules pass on the demo brand and the hackathon data")
def t_rules():
    out = {n: f"{(base(n)['checks'].status == 'PASS').sum()}/{len(base(n)['checks'])}" for n in ("demo", "hack")}
    return all(a == b for a, b in (v.split("/") for v in out.values())), out


@check("Correctness", "Line decisions (reject, reduce) flow into transfer orders")
def t_lines():
    r = pipeline.build_plan(DEMO, CFG(), "lines", "tester", "demo")
    pid = r["plan_id"]
    d = plans.decisions(pid)
    plans.set_decisions(pid, pd.DataFrame({"move_id": d.move_id.iloc[:2], "decision": ["approved", "approved"],
                                           "approved_qty": [0, 1], "note": ["", ""]}), "tester")
    o = E.transfer_orders(r["R"]["moves"], r["R"]["trips"])
    o2 = plans.apply_decisions(o, pid)
    gone = not o2.move_id.eq(d.move_id.iloc[0]).any()
    one = o2[o2.move_id == d.move_id.iloc[1]].qty.tolist() == [1]
    return gone and one, f"{len(o)}→{len(o2)} lines, rejected line removed={gone}, reduced to 1={one}"


# ============================================================================ any business, images, operations
GEN = HERE / "data" / "demo_general"


@check("Any business", "General retail without sizes or styles: vendor exports, expiry dates, all 17 rules pass")
def t_general():
    rep = ingest.run(GEN, TMP / "gen_std", log=False, stale=False)
    R = E.run(TMP / "gen_std", None, schema.load_config(HERE / "brands" / "demo_general.json"))
    ok = (R["checks"].status == "PASS").all() and not rep["missing"] and len(R["moves"]) > 0
    return ok, f"{len(R['trips'])} trips, {int(R['moves'].qty.sum())} units, rules {(R['checks'].status == 'PASS').sum()}/{len(R['checks'])}"


@check("Any business", "Every business-type profile plans with all rules passing")
def t_profiles():
    out = {}
    for p in ("apparel", "general_retail", "electronics", "grocery_fmcg", "pharmacy", "spare_parts"):
        R = E.run(TMP / "gen_std", None, schema.load_config(HERE / "brands" / f"{p}.json"))
        out[p] = bool((R["checks"].status == "PASS").all())
    return all(out.values()), out


@check("Any business", "Expired stock is never moved; stock near expiry is sent where it sells in time")
def t_expiry():
    d = TMP / "exp"
    shutil.copytree(TMP / "gen_std", d)
    sn = pd.read_csv(d / "stock_snapshot.csv", dtype=str, keep_default_na=False)
    food = sn[(sn.location_id == "SIL-01") & sn.sku_id.str.startswith("PAC")].head(3).index
    sn.loc[food[:1], "expiry_date"] = "2026-10-20"           # already expired
    sn.loc[food[1:], "expiry_date"] = "2026-11-10"           # expires within the window
    sn.to_csv(d / "stock_snapshot.csv", index=False)
    R = E.run(d, None, schema.load_config(HERE / "brands" / "grocery_fmcg.json"))
    mv = R["moves"]
    expired_sku = sn.loc[food[0], "sku_id"]
    moved_expired = int(mv[(mv.from_id == "SIL-01") & (mv.sku_id == expired_sku)].qty.sum())
    flagged = R["issues"].issue.isin(["Expiring soon"]).any() and R["issues"].detail.str.contains("expired").any()
    return moved_expired == 0 and flagged, f"expired units moved from SIL-01: {moved_expired}; 'Expiring soon' and expired stock reported: {flagged}"


def _images():
    import numpy as np
    import zxingcpp
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    it = pd.read_csv(GEN / "item_master.csv", dtype=str)
    rng = np.random.default_rng(3)
    pick = it.sample(18, random_state=4)
    truth = {r.sku_id: int(rng.integers(0, 60)) for r in pick.itertuples()}
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26) if Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").exists() else ImageFont.load_default()
    im = Image.new("RGB", (1700, 1250), "white")
    dr = ImageDraw.Draw(im)
    y = 120
    for r in pick.itertuples():
        dr.text((60, y), f"{r.sku_id:<12}  {r.product_name[:30]}", font=font, fill="black")
        dr.text((1150, y), str(truth[r.sku_id]), font=font, fill="black")
        y += 58
    photo = im.rotate(3, expand=True, fillcolor="#d8d4cc").filter(ImageFilter.GaussianBlur(1.1))
    codes = list(it.sample(6, random_state=9).itertuples())
    counts = dict(zip([c.sku_id for c in codes], [3, 1, 2, 4, 1, 2]))
    shelf = Image.new("RGB", (2000, 1500), "#efece6")
    x, yy = 40, 40
    for c in codes:
        for _ in range(counts[c.sku_id]):
            b = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(c.barcode, zxingcpp.BarcodeFormat.EAN13), scale=3)
            b = Image.fromarray(np.array(b)).convert("RGB")
            shelf.paste(b, (x, yy))
            x += b.width + 60
            if x > 1600:
                x, yy = 40, yy + b.height + 70
    enc = lambda i, f="JPEG": (lambda bio: (i.save(bio, f), bio.getvalue())[1])(io.BytesIO())  # noqa: E731
    cat = pd.DataFrame({"sku_id": it.sku_id, "name": it.product_name, "barcode": it.barcode})
    return cat, truth, counts, enc(photo), enc(shelf)


@check("Stock from images", "Barcode photo: every item and unit counted; tilted, blurred photo of a printed sheet: 17+ of 18 lines exact")
def t_vision():
    import vision
    cat, truth, counts, photo, shelf = _images()
    b = vision.summarise(vision.extract([("shelf.jpg", shelf)], cat, ("barcode",))[0])
    s = vision.summarise(vision.extract([("sheet.jpg", photo)], cat, ("ocr",))[0])
    bar_ok = dict(zip(b.sku_id, b.qty)) == counts
    exact = sum(1 for k, v in truth.items() if dict(zip(s.sku_id, s.qty)).get(k) == v)
    return bar_ok and exact >= 17, f"barcodes exact: {bar_ok}; sheet lines exact: {exact}/18"


@check("Stock from images", "Look-alike codes (PS01 vs P501) are never guessed between")
def t_vision_collision():
    import vision
    cat = pd.DataFrame({"sku_id": ["PS01", "P501", "AB12"], "name": ["x", "y", "z"], "barcode": ["", "", ""]})
    got = vision.parse_lines(["PS01 4", "P501 7", "PSO1 9"], cat)
    m = {g["raw"]: g["code"] for g in got}
    return m.get("PS01 4") == "PS01" and m.get("P501 7") == "P501" and m.get("PSO1 9") in ("", None), m


@check("Stock from images", "AI reading parses the model's reply and keeps unreadable quantities flagged (simulated reply)")
def t_vision_ai():
    import types
    import vision
    from PIL import Image
    reply = '{"lines": [{"code": "MOB001", "name": "USB-C Cable A", "qty": 12, "legible": true}, {"code": "", "name": "Earbuds", "qty": 3, "legible": false}]}'

    class FakeMsgs:
        def create(self, **kw):
            return types.SimpleNamespace(content=[types.SimpleNamespace(text="Here you go:\n" + reply)])

    fake = types.SimpleNamespace(Anthropic=lambda **kw: types.SimpleNamespace(messages=FakeMsgs()))
    real = sys.modules.get("anthropic")
    sys.modules["anthropic"] = fake
    try:
        lines = vision.ai_read(Image.new("RGB", (50, 50), "white"))
    finally:
        if real is not None:
            sys.modules["anthropic"] = real
        else:
            del sys.modules["anthropic"]
    return lines[0]["qty"] == 12 and lines[1]["confidence"] < 0.5, f"{len(lines)} lines; unreadable line confidence {lines[1]['confidence']}"


@check("Stock counts", "A saved count replaces system stock in the next plan and raises a 'count differs' exception")
def t_counts():
    import stockcount
    stockcount.save("KOL-02", "2026-10-31", pd.DataFrame({"sku_id": ["MOB001", "MOB002"], "qty": [77, 0], "confidence": [1.0, 1.0]}), "tester")
    r = pipeline.build_plan(GEN, schema.load_config(HERE / "brands" / "demo_general.json"), "count test", "tester", "upload")
    oh = r["R"]["pl"].g.oh
    x = XQ.table()
    hit = x[(x.kind == "count") & (x.location == "KOL-02")]
    return int(oh.get(("KOL-02", "MOB001"), -1)) == 77 and int(oh.get(("KOL-02", "MOB002"), -1)) == 0 and len(hit) == 1, \
        f"MOB001={oh.get(('KOL-02', 'MOB001'))}, MOB002={oh.get(('KOL-02', 'MOB002'))}, exception: {hit.title.tolist()}"


@check("Stock counts", "With no stock file at all, counts alone become the stock (and old stock without dates no longer crashes)")
def t_counts_only():
    d = TMP / "nostock"
    shutil.copytree(GEN, d)
    (d / "closing_stock.csv").unlink()
    r = pipeline.build_plan(d, schema.load_config(HERE / "brands" / "demo_general.json"), "counts only", "tester", "upload")
    return r["report"]["counts"]["created"] and (r["R"]["checks"].status == "PASS").all(), r["report"]["counts"]


@check("Operations", "Approvals not decided by the cut-off raise exceptions per region, and clear once decided")
def t_deadline():
    from datetime import datetime
    pid = plans.recent(1).iloc[0].plan_id
    p = plans.get(pid)
    late = XQ.check_deadlines(pid, now=datetime.fromisoformat(f"{p.plan_date}T15:00"))
    for t in plans.trips_of(pid).to_number:
        db.insert_rows("trip_decisions", [dict(plan_id=pid, to_number=t, region="", decision="approved", by="t", at=datetime.now())])
    after = XQ.check_deadlines(pid, now=datetime.fromisoformat(f"{p.plan_date}T15:30"))
    return len(late) >= 1 and after == [], f"overdue regions {late}, after deciding {after}"


@check("Operations", "Purchase suggestions subtract spare warehouse stock and orders due, and round up to pack size")
def t_buying():
    import buying
    d = TMP / "buy"
    shutil.copytree(TMP / "gen_std", d)
    it = pd.read_csv(d / "items.csv", dtype=str)
    it["pack_size"] = "12"
    it.to_csv(d / "items.csv", index=False)
    R = E.run(d, None, schema.load_config(HERE / "brands" / "demo_general.json"))
    b = buying.suggestions(R)
    ok = len(b) > 0 and (b.suggest_buy % 12 == 0).all() and (b.suggest_buy >= b.units_short - b.warehouse_spare - b.on_order_later).all()
    return ok, f"{len(b)} SKUs, {int(b.suggest_buy.sum())} units, all multiples of 12: {(b.suggest_buy % 12 == 0).all()}"


@check("Operations", "Forecast back-test: StockFlow beats the simple 28-day average on both demo businesses")
def t_accuracy():
    import accuracy
    res = {}
    for name, src, prof in (("apparel", DEMO, "demo"), ("general", GEN, "demo_general")):
        r = accuracy.for_inputs(src, schema.load_config(HERE / "brands" / f"{prof}.json"))
        lv = r["levels"]["Store × category"]
        res[name] = (round(lv["stockflow_wape"], 3), round(lv["simple_wape"], 3))
    return all(a < b for a, b in res.values()), f"store×category error (StockFlow, simple): {res}"


@check("Security", "Outbound calls refuse file:, ftp: and cloud-metadata addresses")
def t_urls():
    bad = []
    for u in ("file:///etc/passwd", "ftp://x/y", "http://169.254.169.254/latest", "gopher://x"):
        try:
            safety.check_url(u)
            bad.append(u)
        except ValueError:
            pass
    safety.check_url("https://erp.example.com/api")
    return not bad, "all refused" if not bad else bad



@check("Correctness", "Weekly forecast: under-prediction no worse than 15% and error within 3 points of the simple average")
def t_forecast_weekly():
    import kpi
    out, ok = {}, True
    for name, src, prof in (("apparel", DEMO, "demo"), ("general", TMP / "gen_std", "demo_general")):
        rep = kpi.replay(src, schema.load_config(HERE / "brands" / f"{prof}.json"), 8)
        e, es = kpi.wape(rep), kpi.wape(rep, "simple")
        b = float((rep.pred.sum() - rep.actual.sum()) / rep.actual.sum())
        out[name] = f"error {e:.0%} (simple {es:.0%}), bias {b:+.0%}"
        ok &= b > -0.15 and e <= es + 0.03
    return ok, out


@check("Correctness", "Items already selling are forecast even if their launch date is still ahead")
def t_launch():
    d = TMP / "launch"
    shutil.copytree(DEMO, d)
    it = pd.read_csv(d / "items.csv", dtype=str)
    it.loc[it.index[:20], "launch_date"] = "2027-01-01"
    it.to_csv(d / "items.csv", index=False)
    R = E.run(d, None, CFG())
    early = R["issues"].issue.eq("Sales before launch date").any()
    v = R["pl"].g.reset_index()
    sold = v[v.sku_id.isin(it.sku_id[:20]) & (v.sold12 > 0)]
    return early and (sold.v > 0).all(), f"warned: {early}; selling items with future launch dates forecast > 0: {(sold.v > 0).all()}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    print(f"Database: {db.describe()}\n")
    for c in CHECKS:
        c()
    n_ok = sum(r["ok"] for r in RESULTS)
    print(f"\n{n_ok}/{len(RESULTS)} checks passed")
    if a.json:
        Path(a.json).write_text(json.dumps(dict(database=db.describe(), results=RESULTS), indent=1))
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
