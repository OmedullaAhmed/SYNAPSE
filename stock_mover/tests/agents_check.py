"""Agent checks: every agent drafts with AI off, approval runs only whitelisted actions with role checks and audit, and the
Claude tool-use loop works against a fake client (no network, no key).

    python tests/agents_check.py            # throwaway SQLite database
"""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/agents_check.db"

import agents as A  # noqa: E402
import audit  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import engine as E  # noqa: E402
import exceptions_queue as XQ  # noqa: E402
import feedback as fb  # noqa: E402
import ops_agents as O  # noqa: E402
import pipeline  # noqa: E402
import schema  # noqa: E402

RESULTS, CHECKS, S = [], [], {}


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


class FakeClient:
    """Answers like the Anthropic SDK: first asks for a tool, then replies with text (or JSON when asked)."""
    def __init__(self, tool=None, tool_input=None, text="AI draft. Resolution note: checked with the store.", json_text=None):
        self.tool, self.tool_input, self.text, self.json_text, self.calls = tool, tool_input or {}, text, json_text, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.json_text and "JSON object only" in kw["system"]:
            return NS(content=[NS(type="text", text=self.json_text)])
        if self.tool and kw.get("tools") and len(self.calls) == 1:
            return NS(content=[NS(type="tool_use", id="t1", name=self.tool, input=self.tool_input)])
        return NS(content=[NS(type="text", text=self.text)])


def can(role):
    return lambda perm: auth.allowed(role, perm)


def setup():
    cfg = schema.load_config(HERE / "brands" / "demo_general.json")
    out = pipeline.build_plan(HERE / "data" / "demo_general", cfg, "Demo Mart (fictional general retailer)", "tester", kind="demo")
    R, pid = out["R"], out["plan_id"]
    o = E.transfer_orders(R["moves"], R["trips"])
    tos = list(o.to_number.unique()[:3])
    fb.release_orders(o[o.to_number.isin(tos)], "tester", pid)
    first = o[o.to_number == tos[0]]
    fb.mark_dispatched(tos[0], "tester")
    short = {s: max(0, int(q) - 1) for s, q in first.groupby("sku_id").qty.sum().items()}
    fb.mark_received(tos[0], "tester", short)
    XQ.sync_transfers("tester")
    S.update(R=R, pid=pid, cfg=cfg, tos=tos, orders=o)


@check("Morning brief drafts one per region plus an overall brief (AI off)")
def t_brief():
    ids = O.run_brief("tester", S["pid"])
    d = A.drafts(kind="brief")
    ok = len(ids) >= 2 and d.source.eq("rules").all() and d.body.str.contains("Plan").any()
    return ok, f"{len(ids)} briefs; first: {d.iloc[-1].body.splitlines()[0][:90]}"


@check("Re-running an agent replaces its older pending drafts")
def t_supersede():
    O.run_brief("tester", S["pid"])
    d = A.drafts(status=None, kind="brief")
    return (d.status == "pending").sum() == (d.status == "rejected").sum(), f"{(d.status == 'pending').sum()} pending, {(d.status == 'rejected').sum()} replaced"


@check("Exception investigator drafts a cause, fix and resolution for the short receipt")
def t_fixer():
    x = XQ.table()
    ids = O.run_fixer("tester")
    d = A.drafts(kind="fix")
    short = d[d.title.str.contains("received short")]
    return len(ids) == min(len(x), 15) and len(short) == 1 and "Likely cause" in short.iloc[0].body, f"{len(ids)} drafts; {short.iloc[0].title[:80] if len(short) else '-'}"


@check("Approving a fix resolves the exception; store role is refused; both audited")
def t_approve():
    d = A.drafts(kind="fix").iloc[0]
    try:
        A.decide(d.id, "store1", "store", True, can=can("store"))
        refused = False
    except PermissionError:
        refused = True
    msg = A.decide(d.id, "boss", "admin", True, can=can("admin"))
    xid = d.payload["ids"][0]
    x = db.read("exceptions", {"id": xid}).iloc[0]
    a = audit.as_frame()
    ok = refused and x.status == "resolved" and (a.action == "agent.approve").any()
    try:
        A.decide(d.id, "boss", "admin", True, can=can("admin"))
        twice = False
    except ValueError:
        twice = True
    return ok and twice, f"{msg}; store refused={refused}; second approval refused={twice}"


@check("Transfer follow-up drafts nudges for late transfers (no phone on file -> information only)")
def t_chaser():
    ids = O.run_chaser("tester", statuses=("Waiting to leave", "Late leaving", "Late arriving", "In transit"))
    d = A.drafts(kind="chase")
    return len(ids) >= 1 and (d.action == "").all() and d.body.str.contains("Has it").all(), f"{len(ids)} nudges; {d.iloc[0].title[:70]}"


@check("WhatsApp replies become proposed updates: 'sent all', 'received 10, 2 damaged', per-SKU, 'not yet'")
def t_reply():
    to = S["tos"][1]
    a = O.parse_reply(f"{to} sent all")
    lines = S["orders"][S["orders"].to_number == to].groupby("sku_id").qty.sum()
    tot = int(lines.sum())
    b = O.parse_reply(f"{to} received {tot}, 2 damaged")
    sku = lines.index[0]
    c = O.parse_reply(f"{to} got {sku} 1")
    n = O.parse_reply(f"{to} not yet, van broke down")
    ok = (a["event"] == "dispatched" and a["lines"] is None and b["event"] == "received" and sum(b["lines"].values()) == tot - 2
          and b["damaged"] == 2 and c["lines"] == {sku: 1} and n["event"] is None)
    did = O.handle_reply(f"{to} sent all", phone="+91 98300 12345")
    dd = db.read("agent_drafts", {"id": did}).iloc[0]
    A.decide(did, "boss", "admin", True, can=can("admin"))
    t, _ = fb.tracking()
    st = t.set_index("to_number").status[to]
    return ok and dd.action == "record_transfer" and st in ("In transit", "Late arriving"), f"{to}: {st}; damaged split ok; number shown masked: {'…2345' in dd.title or '…2345' in dd.body or 'from' in dd.title}"


@check("Supplier buying drafts one purchase order per vendor; approval stores the PO")
def t_buyer():
    ids = O.run_buyer("tester", S["pid"])
    if not ids:
        return True, "no buy suggestions in this plan (nothing to order)"
    d = A.drafts(kind="buy").iloc[0]
    A.decide(d.id, "boss", "admin", True, can=can("admin"))
    po = db.get_setting(f"po:{d.payload['po_id']}"[:40])
    return po is not None and po["approved_by"] == "boss" and "Subject:" in d.body, f"{len(ids)} POs; {d.title[:80]}"


@check("Data onboarding explains a messy upload and suggests a mapping")
def t_onboard():
    import pandas as pd
    src = Path(tempfile.mkdtemp())
    s = pd.read_csv(HERE / "data" / "demo_general" / "pos_sales_export.csv").head(300)
    s.rename(columns={"Qty": "Qnty Sold"}).to_csv(src / "sales.csv", index=False)
    ids, rep = O.run_onboard(src, "tester", label="test")
    d = A.drafts(kind="onboard")
    body = d[d.title.str.startswith("Onboarding")].iloc[0].body
    return "Missing" in body and "sales" in body, f"{len(ids)} drafts; {body.splitlines()[0][:90]}"


@check("What-if: closing a store and +20% demand rerun the planner and compare (AI off)")
def t_whatif():
    stores = S["R"]["pl"].st.index.tolist()
    t0 = time.time()
    did, sc, a, b = O.run_whatif(f"what if we close {stores[0]} and demand rises 20%", "tester", S["pid"], cfg=S["cfg"])
    d = db.read("agent_drafts", {"id": did}).iloc[0]
    ok = sc["close"] == [stores[0]] and sc["demand"][0]["pct"] == 20 and b["pred7"] > a["pred7"] and "Scenario" in d.body
    return ok, f"{describe(sc)} in {time.time() - t0:.0f}s; forecast 7d {a['pred7']:,.0f} -> {b['pred7']:,.0f}"


def describe(sc):
    return O.describe(sc)


@check("Scenario parser: add a store like X, category demand, settings, nonsense")
def t_parse():
    st, cats = ["KOL-SL", "PAT-01"], ["Dolls", "Board Games"]
    a = O.parse_scenario("add a store like PAT-01 called PAT-02; demand -10% in dolls; cover days 21", st, cats)
    b = O.parse_scenario("tell me a joke", st, cats)
    ok = a["add"] == [dict(like="PAT-01", id="PAT-02")] and a["demand"] == [dict(pct=-10.0, category="Dolls")] \
        and a["settings"] == {"planning.cover_days": 21} and O.describe(b) == "no change"
    return ok, O.describe(a)


@check("AI path: Claude tool loop calls a read-only tool, then drafts (fake client, AI switched on)")
def t_ai_loop():
    A.set_ai(True, "boss")
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    x = XQ.table()
    xid = int(x.iloc[0].id)
    to = S["tos"][0]
    fc = FakeClient(tool="transfer_lines", tool_input={"to_number": to})
    ids = O.run_fixer("tester", client=fc, ids=[xid])
    d = db.read("agent_drafts", {"id": ids[0]}).iloc[0]
    tool_res = fc.calls[1]["messages"][-1]["content"][0]
    ok = d.source == "ai" and d.payload["note"] == "checked with the store." and tool_res["type"] == "tool_result" and len(fc.calls) == 2
    A.set_ai(False, "boss")
    return ok, f"{len(fc.calls)} model calls; note='{d.payload['note']}'"


@check("AI off by default; unknown actions are refused; an AI error falls back to rules")
def t_safety():
    on, _ = A.ai_status()
    try:
        A.draft("x", "t", "b", action="delete_everything")
        bad = False
    except ValueError:
        bad = True

    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("network down")
    ids = O.run_brief("tester", S["pid"], client=Boom(), per_region=False)
    d = db.read("agent_drafts", {"id": ids[0]}).iloc[0]
    return (not on) and bad and d.source == "rules" and "AI was not available" in d.body, "default off; whitelist enforced; fallback ok"


@check("API: list agents, run brief as a job, decide a draft; store users see only their drafts; phones hidden")
def t_api():
    from fastapi.testclient import TestClient
    import api
    c = TestClient(api.app)
    H = lambda role, stores="": {"Authorization": "Bearer " + api.make_token("u_" + role, role, stores)}  # noqa: E731
    import unittest.mock as um
    recs = {"u_admin": dict(role="admin", active=True), "u_store": dict(role="store", stores="ZZ-01", active=True)}
    with um.patch.object(auth, "all_users", lambda *_a, **_k: recs):
        a = c.get("/api/v1/agents", headers=H("admin")).json()
        j = c.post("/api/v1/agents/brief/run", json={}, headers=H("admin")).json()
        for _ in range(60):
            s = c.get(f"/api/v1/jobs/{j['job_id']}", headers=H("admin")).json()
            if s["status"] in ("done", "failed"):
                break
            time.sleep(0.5)
        d = c.get("/api/v1/agents/drafts?kind=brief", headers=H("admin")).json()
        mine = c.get("/api/v1/agents/drafts", headers=H("store", "ZZ-01")).json()
        r = c.post(f"/api/v1/agents/drafts/{d[0]['id']}", json={"approve": False}, headers=H("admin")).json()
        forbidden = c.post("/api/v1/agents/whatif/run", json={"text": "close X"}, headers=H("store", "ZZ-01")).status_code
        has_phone = any("phone" in (x.get("payload") or {}) for x in c.get("/api/v1/agents/drafts?status=pending,done", headers=H("admin")).json())
    ok = len(a["agents"]) == 6 and s["status"] == "done" and len(d) >= 1 and mine == [] and r["result"] == "Rejected." and forbidden == 403 and not has_phone
    return ok, f"job {s['status']}; {len(d)} brief drafts; store sees {len(mine)}; what-if for store -> {forbidden}"


@check("WhatsApp inbound: unsigned/bad signatures refused; signed Meta and Twilio messages become drafts")
def t_wa():
    import hashlib
    import hmac
    import json as _j
    from fastapi.testclient import TestClient
    import api
    c = TestClient(api.app)
    api.SECRETS = {"whatsapp": {"app_secret": "s3cret", "auth_token": "tw1l", "inbound_url": "https://x.test/api/v1/whatsapp/inbound",
                                "verify_token": "vt"}}
    to = S["tos"][2]
    body = _j.dumps({"entry": [{"changes": [{"value": {"messages": [{"from": "919830012345", "type": "text",
                                                                    "text": {"body": f"{to} sent all"}}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    unsigned = c.post("/api/v1/whatsapp/inbound", content=body).status_code
    bad = c.post("/api/v1/whatsapp/inbound", content=body, headers={"X-Hub-Signature-256": "sha256=00"}).status_code
    good = c.post("/api/v1/whatsapp/inbound", content=body, headers={"X-Hub-Signature-256": sig}).json()
    form = {"From": "whatsapp:+919830012345", "Body": f"{to} received all"}
    tsig = api.twilio_signature("tw1l", "https://x.test/api/v1/whatsapp/inbound", form)
    tw = c.post("/api/v1/whatsapp/inbound", data=form, headers={"X-Twilio-Signature": tsig}).json()
    ver = c.get("/api/v1/whatsapp/inbound?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=42").json()
    d = db.read("agent_drafts", {"id": good["drafts"][0]}).iloc[0]
    api.SECRETS = {}
    return unsigned == 403 and bad == 403 and d.action == "record_transfer" and len(tw["drafts"]) == 1 and ver == 42, \
        f"unsigned {unsigned}, bad {bad}; Meta -> {d.title[:60]}; Twilio -> draft {tw['drafts'][0]}; verify {ver}"


def main():
    t = time.time()
    setup()
    print(f"setup {time.time() - t:.0f}s")
    for name, fn in CHECKS:
        try:
            ok, ev = fn()
        except Exception as e:  # noqa: BLE001
            ok, ev = False, f"{type(e).__name__}: {e} | {traceback.format_exc().splitlines()[-3].strip()}"
        RESULTS.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name} — {str(ev)[:200]}", flush=True)
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
