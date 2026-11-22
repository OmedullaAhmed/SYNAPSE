"""The six operations agents. Each one gathers facts with read-only code, writes drafts (agents.draft), and never acts.

  brief      morning operations brief per region: approvals waiting, urgent refills, late transfers, open problems, buys
  fixer      exception investigator: evidence, likely cause, fix at source, a proposed resolution note
  chaser     transfer follow-up: WhatsApp nudges for late transfers; turns replies ("sent 4, 2 damaged") into proposed updates
  buyer      supplier buying: purchase orders by vendor (pack size, minimum order) and the email to send
  onboard    data onboarding: explains what each file was read as, what is missing, and suggests column mappings
  whatif     what-if planner: "close KOL-SL", "demand +20% in toys", "add a store like PAT-01" -> rerun and compare

With AI on, Claude writes the text (and, for fixer/whatif/chaser replies, reads facts through read-only tools). With AI
off, the same drafts come from rules and templates, so every agent works offline and in tests.
"""
import json
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import agents as A
import db
import engine as E
import ingest
import kpi
import notify
import pipeline
import schema
import exceptions_queue as XQ
import feedback as fb
import plans

HERE = Path(__file__).resolve().parent
STYLE = ("You work inside StockFlow, a stock planning app for businesses with several warehouses and stores. Write in plain "
         "English for busy operations staff: short sentences, numbers first, no jargon, no markdown tables, at most 180 words. "
         "Use only the facts given or returned by tools; if something is unknown, say so. Never invent numbers.")


def _latest_plan():
    p = plans.recent(1)
    return None if p.empty else p.iloc[0]


def _trip_state(plan_id):
    t = plans.trips_of(plan_id)
    if t.empty:
        return t
    locs = XQ.locations()
    t["region"] = [(locs.get(b) or {}).get("zone") or (locs.get(a) or {}).get("zone") or "—" for a, b in zip(t.from_id, t.to_id)]
    d = db.read("trip_decisions", {"plan_id": plan_id})
    t["decision"] = t.to_number.map(d.set_index("to_number").decision) if len(d) else None
    t["decision"] = t.decision.fillna("pending")
    rel = fb.released()
    done = set(rel[rel.run_id == plan_id].to_number) if len(rel) and "run_id" in rel else set()
    t["released"] = t.to_number.isin(done)
    return t


def _use_ai(secrets, client):
    if getattr(A._LOCAL, "off", False):
        return False, None
    on, key = A.ai_status(secrets)
    return (on and bool(key)) or client is not None, key


def _write(facts, task, fallback, secrets=None, client=None, tools=None):
    """(text, source): Claude's text when AI is on, otherwise the rule-based fallback text."""
    ai, key = _use_ai(secrets, client)
    if not ai:
        return fallback, "rules"
    try:
        text, _ = A.run_ai(STYLE, f"{task}\n\nFACTS (JSON):\n{json.dumps(facts, default=str)[:30000]}", tools=tools, key=key, client=client)
        return (text or fallback), "ai"
    except Exception as e:  # noqa: BLE001 - AI trouble never blocks the agent
        return fallback + f"\n\n(AI was not available: {type(e).__name__}. This draft was written from rules.)", "rules"


def _money(x):
    x = float(x or 0)
    return f"₹{x / 1e5:,.1f} lakh" if abs(x) >= 1e5 else f"₹{x:,.0f}"


# ============================================================================ 1. morning brief
def brief_facts(plan_id=None):
    p = plans.get(plan_id) if plan_id else _latest_plan()
    facts = dict(plan=None, regions={}, transfers={}, exceptions={}, buying=[], kpi={})
    if p is None:
        return facts
    pid = p.plan_id
    facts["plan"] = dict(plan_id=pid, plan_date=p.plan_date, trips=int(p.trips), pieces=int(p.pieces), net_benefit=float(p.net_benefit))
    t = _trip_state(pid)
    if len(t):
        for reg, g in t.groupby("region"):
            pend = g[g.decision == "pending"]
            facts["regions"][reg] = dict(trips=len(g), pending=len(pend), urgent_pending=int((pend.urgent > 0).sum()),
                                         pieces_pending=int(pend.pieces.sum()), value_pending=float(pend.value_mrp.sum()),
                                         released=int(g.released.sum()),
                                         biggest=[dict(to=r.to_number, route=f"{r.from_id}→{r.to_id}", pieces=int(r.pieces))
                                                  for r in pend.sort_values("pieces", ascending=False).head(3).itertuples()])
    tr, _ = fb.tracking()
    locs = XQ.locations()
    if tr is not None and len(tr):
        tr = tr.assign(region=[(locs.get(b) or {}).get("zone") or (locs.get(a) or {}).get("zone") or "—"
                               for a, b in zip(tr.from_location, tr.to_location)])
        for reg, g in tr.groupby("region"):
            facts["transfers"][reg] = {k: int(v) for k, v in g.status.value_counts().items()}
    x = XQ.table()
    if len(x):
        for reg, g in x.assign(region=x.region.fillna("").replace("", "—")).groupby("region"):
            facts["exceptions"][reg] = dict(open=len(g), high=int((g.severity == "high").sum()),
                                            top=[f"{r.title}" for r in g.head(3).itertuples()])
    b = db.get_setting(f"buy:{pid}"[:40], []) or []
    facts["buying"] = [dict(sku=r["sku_id"], name=r.get("name"), buy=r["suggest_buy"], cost=r.get("cost_value")) for r in b[:5]]
    facts["buying_total"] = float(sum(r.get("cost_value") or 0 for r in b))
    h = db.read("kpi_history", {"plan_id": pid})
    if len(h):
        facts["kpi"] = dict(in_stock=round(float(h.in_stock.sum() / max(h.selling.sum(), 1)), 3), at_risk=int(h.at_risk.sum()),
                            on_hand=int(h.on_hand.sum()), pred7=round(float(h.pred7.sum())))
    return facts


def brief_text(facts, region=None):
    if not facts.get("plan"):
        return "No plan yet. Upload today's files or run a demo to get a brief."
    p = facts["plan"]
    regs = [region] if region else sorted(set(facts["regions"]) | set(facts["transfers"]) | set(facts["exceptions"]))
    L = [f"Good morning. Plan {p['plan_date']}: {p['trips']} trips, {p['pieces']:,} pieces, expected benefit {_money(p['net_benefit'])}."]
    if facts.get("kpi"):
        k = facts["kpi"]
        L.append(f"In stock on selling items: {k['in_stock']:.0%}; {k['at_risk']:,} store items may run out before a refill arrives.")
    for r in regs:
        g, tr, ex = facts["regions"].get(r, {}), facts["transfers"].get(r, {}), facts["exceptions"].get(r, {})
        bits = []
        if g.get("pending"):
            bits.append(f"{g['pending']} trips to approve ({g['urgent_pending']} urgent, {g['pieces_pending']:,} pieces)")
        late = tr.get("Late leaving", 0) + tr.get("Late arriving", 0)
        if late:
            bits.append(f"{late} transfers late")
        short = tr.get("Received short", 0)
        if short:
            bits.append(f"{short} received short")
        if ex.get("open"):
            bits.append(f"{ex['open']} open problems ({ex['high']} high)")
        if bits:
            L.append(f"• {r}: " + "; ".join(bits) + ".")
        elif region:
            L.append(f"• {r}: nothing waiting. ")
    if facts.get("buying") and not region:
        top = ", ".join(f"{b['buy']} × {b['name'] or b['sku']}" for b in facts["buying"][:3])
        L.append(f"Suppliers: buy suggestions worth {_money(facts['buying_total'])} (top: {top}).")
    first = next((f"approve {g['biggest'][0]['to']} ({g['biggest'][0]['route']})" for r in regs
                  for g in [facts["regions"].get(r, {})] if g.get("urgent_pending") and g.get("biggest")), None)
    L.append(f"First thing to do: {first}." if first else "Nothing urgent is waiting.")
    return "\n".join(L)


def run_brief(user="agent", plan_id=None, secrets=None, client=None, per_region=True):
    facts = brief_facts(plan_id)
    if not facts.get("plan"):
        return []
    ids = []
    regs = sorted(set(facts["regions"]) | set(facts["exceptions"]) | set(facts["transfers"])) if per_region else []
    for reg in [None] + regs:
        ref = f"brief:{facts['plan']['plan_id']}:{reg or 'all'}"
        A.supersede("brief", ref)
        sub = facts if reg is None else dict(facts, regions={reg: facts["regions"].get(reg, {})}, transfers={reg: facts["transfers"].get(reg, {})},
                                             exceptions={reg: facts["exceptions"].get(reg, {})})
        text, src = _write(sub, "Write this morning's operations brief" + (f" for region {reg}" if reg else " for the whole business")
                           + ": what needs a decision, what is late, what to do first.", brief_text(facts, reg), secrets, client)
        ids.append(A.draft("brief", f"Morning brief — {reg or 'all regions'} — {facts['plan']['plan_date']}", text, "post_brief",
                           {"region": reg or ""}, region=reg or "", ref=ref, agent="Morning brief", source=src))
    return ids


# ============================================================================ 2. exception investigator
def _evidence(r):
    ev = dict(id=int(r.id), kind=r.kind, severity=r.severity, title=r.title, detail=r.detail, location=r.location, ref=r.ref,
              first_seen=str(r.first_seen), seen_count=int(r.seen_count or 1), value=float(r.value or 0))
    if r.kind == "transfer" and r.ref:
        _, lines = fb.tracking()
        if lines is not None and len(lines):
            L = lines[lines.to_number == r.ref]
            ev["lines"] = A.frame(L[["sku_id", "planned", "sent", "received", "gap"]], 30)
    elif r.kind == "count":
        c = db.read("stock_counts", {"location": r.location}) if r.location else pd.DataFrame()
        if len(c):
            ev["counts"] = A.frame(c.sort_values("id").tail(20)[[k for k in ("counted_on", "sku_id", "qty", "method", "by") if k in c]], 20)
    elif r.kind == "data":
        ing = db.read("ingestions", order=["-id"], limit=20)
        if len(ing):
            ev["recent_files"] = A.frame(ing[["at", "file", "sheet", "status", "rows_in", "rows_out"]], 10)
    return ev


RULES = {  # kind -> (likely cause, what to do)
    "transfer": {"Received short": ("Pieces lost, damaged or mis-picked between the two locations, or the receipt was counted short.",
                                    "Ask the sender to confirm what was packed; recount the receiving store; write off true losses."),
                 "Received over": ("More was packed than planned, or another order's stock arrived in the same parcel.",
                                   "Check the sender's dispatch note; book the extra pieces to the right order."),
                 "Late leaving": ("The sending location has not packed it, or forgot to mark it as sent.",
                                  "Call the sender today; if it left, record the dispatch."),
                 "Late arriving": ("The parcel is still on the road, or arrived but nobody recorded the receipt.",
                                   "Check with the receiving store; if it arrived, record the receipt.")},
    "count": ("The system stock and the shelf count differ: unrecorded sales, returns, theft, or a mis-count.",
              "Recount the biggest differences; if confirmed, post a stock adjustment in your stock system."),
    "data": ("A file was missing, late, or in an unexpected layout.", "Re-send the file; save a column mapping if the layout changed."),
    "plan": ("The stock or product data disagrees with itself (for example negative stock or an unknown location).",
             "Correct it in the source system; the next plan will clear it."),
    "approval": ("Trips were not approved before the cut-off.", "Approve or reject the remaining trips; set a backup approver."),
}


def fix_text(ev):
    rule = RULES.get(ev["kind"], ("Unknown.", "Review it."))
    if isinstance(rule, dict):
        rule = next((v for k, v in rule.items() if k.lower() in ev["title"].lower()), ("Unknown.", "Review it."))
    cause, fix = rule
    extra = ""
    if ev.get("lines"):
        gaps = [l for l in ev["lines"] if l.get("gap")]
        extra = (" Lines with a gap: " + ", ".join(f"{l['sku_id']} ({l['gap']:+d})" for l in gaps[:6]) + ".") if gaps else ""
    note = f"Checked by agent: {cause} Action: {fix}"
    return (f"{ev['title']} (seen {ev['seen_count']}×, since {ev['first_seen'][:10]}).\n{ev['detail']}{extra}\n\n"
            f"Likely cause: {cause}\nWhat to do: {fix}\n\nProposed resolution note:\n{note}"), note


def run_fixer(user="agent", secrets=None, client=None, limit=15, ids=None):
    x = XQ.table()
    if x.empty:
        return []
    if ids:
        x = x[x.id.isin([int(i) for i in ids])]
    out = []
    tools = {
        "transfer_lines": ("Planned, sent and received pieces per SKU line of a released transfer order.",
                           {"type": "object", "properties": {"to_number": {"type": "string"}}, "required": ["to_number"]},
                           lambda to_number: A.frame(fb.tracking()[1].query("to_number == @to_number"), 40)),
        "count_history": ("Recent stock counts saved for a location.",
                          {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]},
                          lambda location: A.frame(db.read("stock_counts", {"location": location}), 40)),
    }
    for r in x.head(limit).itertuples():
        ref = f"fix:{int(r.id)}"
        A.supersede("fix", ref)
        ev = _evidence(r)
        fallback, note = fix_text(ev)
        text, src = _write(ev, "Investigate this stock exception. Give: what happened (with numbers), the likely cause, what to do at "
                           "source, and end with a line 'Resolution note:' followed by a one-sentence note for the record.",
                           fallback, secrets, client, tools)
        if src == "ai":
            m = re.search(r"Resolution note:\s*(.+)", text, re.S)
            note = (m.group(1).strip() if m else text)[:1500]
        out.append(A.draft("fix", f"Proposed fix — {r.title}"[:300], text, "resolve_exceptions", {"ids": [int(r.id)], "note": note},
                           region=r.region or "", location=r.location or "", ref=ref, agent="Exception investigator", source=src))
    return out


# ============================================================================ 3. transfer chaser (WhatsApp)
def chase_text(r, who_end):
    if r.status == "Late leaving":
        return (f"Hello {who_end}, transfer {r.to_number} to {r.to_location} ({int(r.planned)} pieces) was due to leave on "
                f"{r.ship_date}. Has it left? Reply 'sent all', 'sent 12' or 'not yet' with the reason. Thank you.")
    return (f"Hello {who_end}, transfer {r.to_number} from {r.from_location} ({int(r.sent or r.planned)} pieces) was due on "
            f"{r.expected}. Has it arrived? Reply 'received all', 'received 10, 2 damaged' or 'not arrived'. Thank you.")


def run_chaser(user="agent", secrets=None, client=None, statuses=("Late leaving", "Late arriving")):
    tr, _ = fb.tracking()
    if tr is None or not len(tr):
        return []
    locs = XQ.locations()
    out = []
    for r in tr[tr.status.isin(statuses)].itertuples():
        end = r.from_location if r.status == "Late leaving" else r.to_location
        ref = f"chase:{r.to_number}:{r.status}"
        A.supersede("chase", ref)
        ph = notify.phone((locs.get(end) or {}).get("whatsapp", ""))
        text = chase_text(r, end)
        body = text if ph else text + "\n\n(No WhatsApp number on file for " + end + ": add a whatsapp column to the store or warehouse list.)"
        out.append(A.draft("chase", f"Chase {r.to_number} — {r.status.lower()} — {end}", body, "send_whatsapp" if ph else None,
                           {"phone": ph, "who": end, "role": "Store" if r.status != "Late leaving" else "Sender", "to_number": r.to_number}
                           if ph else {"to_number": r.to_number}, region=(locs.get(end) or {}).get("zone", ""), location=end, ref=ref,
                           agent="Transfer follow-up"))
    return out


WORDS = {"dispatched": r"\b(sent|dispatch\w*|shipped|left|packed and sent)\b", "received": r"\b(receiv\w*|got|arrived|delivered)\b"}


def parse_reply(text, to_number=None, location=None):
    """A manager's free-text reply -> dict(to_number, event, lines {sku: qty} or None (= all), damaged, note) or None.
    Understands: 'sent all', 'sent 12', 'received 10, 2 damaged', 'got SKU1 4 SKU2 3', 'TO-261122-004 received all'."""
    t = str(text or "").strip()
    low = t.lower()
    m = re.search(r"\bTO-\d{6}-\d{3}\b", t, re.I)
    to_number = (m.group(0).upper() if m else None) or to_number
    tr, lines = fb.tracking()
    if not to_number and location and tr is not None and len(tr):
        open_ = tr[((tr.from_location == location) & tr.status.isin(["Waiting to leave", "Late leaving"])) |
                   ((tr.to_location == location) & tr.status.isin(["In transit", "Late arriving"]))]
        if len(open_) == 1:
            to_number = open_.iloc[0].to_number
    if not to_number:
        return None
    ev = next((e for e, w in WORDS.items() if re.search(w, low)), None)
    if ev is None or re.search(r"\bnot (yet|arrived|sent)\b", low):
        return dict(to_number=to_number, event=None, lines=None, damaged=0, note=t[:300])
    L = lines[lines.to_number == to_number] if lines is not None and len(lines) else pd.DataFrame()
    if len(L):
        G_ = L.groupby("sku_id")[["sent", "planned"]].sum()
        base = G_.sent.where(G_.sent > 0, G_.planned) if ev == "received" else G_.planned
    else:
        base = pd.Series(dtype=int)
    dmg = re.search(r"(\d+)\s*(?:pcs|pieces|units)?\s*(?:damaged|broken|missing|short)", low)
    damaged = int(dmg.group(1)) if dmg else 0
    skus = {s.upper(): s for s in base.index}
    per = {}
    for code, q in re.findall(r"\b([A-Za-z][\w-]{2,})\s*[:x×=-]?\s*(\d+)\b", t):
        if code.upper() in skus:
            per[skus[code.upper()]] = int(q)
    if per:
        return dict(to_number=to_number, event=ev, lines=per, damaged=damaged, note=t[:300])
    if re.search(r"\b(all|everything|full|complete)\b", low) and not damaged:
        return dict(to_number=to_number, event=ev, lines=None, damaged=0, note=t[:300])
    nums = [int(n) for n in re.findall(r"\b(\d+)\b", re.sub(r"\bTO-\d{6}-\d{3}\b", "", t, flags=re.I))]
    total = int(base.sum()) if len(base) else 0
    got = nums[0] if nums else total
    good = max(0, got - damaged) if ev == "received" else got
    # spread a single total over the lines in order (largest first); the person checks it before approving
    rem, per = good, {}
    for s, q in base.sort_values(ascending=False).items():
        take = int(min(q, rem))
        per[s] = take
        rem -= take
    return dict(to_number=to_number, event=ev, lines=per, damaged=damaged, note=t[:300])


def handle_reply(text, phone="", user="whatsapp", secrets=None, client=None):
    """An inbound WhatsApp message -> a draft update for a person to approve. Returns the draft id or None."""
    locs = XQ.locations()
    ph = notify.phone(phone)
    loc = next((k for k, v in locs.items() if ph and notify.phone(v.get("whatsapp", "")) == ph), None)
    p = parse_reply(text, location=loc)
    ai, key = _use_ai(secrets, client)
    if ai and (p is None or p.get("event") is None):
        try:
            tr, lines = fb.tracking()
            ctx = A.frame(tr[(tr.from_location == loc) | (tr.to_location == loc)] if loc and tr is not None and len(tr) else tr, 20)
            j = A.ask_json(STYLE + " Turn a store or warehouse manager's WhatsApp reply into a transfer update. Return "
                           '{"to_number": str|null, "event": "dispatched"|"received"|null, "lines": {sku: qty}|null, "damaged": int, '
                           '"note": str}. lines null means everything as planned.', f"Reply from {loc}: {text}\nOpen transfers: {json.dumps(ctx, default=str)}",
                           key=key, client=client)
            if j.get("to_number"):
                p = dict(to_number=str(j["to_number"]).upper(), event=j.get("event"), lines=j.get("lines"), damaged=int(j.get("damaged") or 0),
                         note=str(j.get("note") or text)[:300])
        except Exception:  # noqa: BLE001
            pass
    shown = f"…{ph[-4:]}" if ph else "unknown number"
    if p is None or not p.get("event"):
        return A.draft("reply", f"WhatsApp reply from {loc or shown} (no update found)", f"Message: {text}\n\nNo dispatch or receipt "
                       "could be read from it. Follow up by phone if needed.", None, {}, location=loc or "", ref=f"reply:{shown}",
                       agent="Transfer follow-up")
    lines = p["lines"]
    what = "everything as planned" if lines is None else ", ".join(f"{k} × {v}" for k, v in lines.items())
    body = (f"Message from {loc or shown}: “{text}”\n\nProposed update: {p['to_number']} {p['event']} — {what}."
            + (f"\n{p['damaged']} pieces reported damaged/missing (not counted as received; raise a claim if needed)." if p["damaged"] else "")
            + "\n\nApprove to record it. Edit the quantities first if they look wrong.")
    return A.draft("reply", f"Update {p['to_number']}: {p['event']} (from WhatsApp)", body, "record_transfer",
                   dict(to_number=p["to_number"], event=p["event"], lines=lines, note=f"WhatsApp: {p['note']}"[:300]),
                   location=loc or "", ref=f"reply:{p['to_number']}", agent="Transfer follow-up")


# ============================================================================ 4. supplier buying
def purchase_orders(plan_id=None):
    p = plans.get(plan_id) if plan_id else _latest_plan()
    if p is None:
        return p, []
    b = pd.DataFrame(db.get_setting(f"buy:{p.plan_id}"[:40], []) or [])
    if b.empty:
        return p, []
    b["vendor"] = b.get("vendor", "").replace("", np.nan).fillna("Unassigned supplier") if "vendor" in b else "Unassigned supplier"
    out = []
    for v, g in b.groupby("vendor"):
        slug = re.sub(r"[^A-Z0-9]+", "", str(v).upper())[:10] or "SUP"
        out.append(dict(po_id=f"PO-{str(p.plan_date).replace('-', '')[2:]}-{slug}", vendor=v, plan_id=p.plan_id,
                        lines=[dict(sku_id=r.sku_id, name=r.get("name"), qty=int(r.suggest_buy), cost=float(r.get("cost_value") or 0))
                               for _, r in g.sort_values("cost_value", ascending=False).iterrows()],
                        total=float(g.cost_value.sum()), units=int(g.suggest_buy.sum())))
    return p, out


def po_email(po, company="our company"):
    rows = "\n".join(f"  {l['sku_id']}  {str(l['name'] or '')[:40]:40}  {l['qty']:>6}" for l in po["lines"][:60])
    return (f"Subject: Purchase order {po['po_id']}\n\nDear {po['vendor']} team,\n\nPlease supply the following "
            f"({po['units']:,} units, about {_money(po['total'])} at our cost prices):\n\n  SKU  Item  Qty\n{rows}\n\n"
            "Please confirm quantities and the delivery date by reply. Deliver to our main warehouse unless told otherwise.\n\n"
            f"Regards,\nPurchasing, {company}")


def run_buyer(user="agent", plan_id=None, secrets=None, client=None, company=None):
    p, pos = purchase_orders(plan_id)
    ids = []
    company = company or (p.brand if p is not None else "our company")
    for po in pos:
        ref = f"po:{po['po_id']}"
        A.supersede("buy", ref)
        fallback = po_email(po, company)
        text, src = _write(po, f"Write a short, polite purchase-order email to supplier {po['vendor']} from {company}. Include a subject "
                           "line, the line list (SKU, item, qty), and ask for confirmation and a delivery date.", fallback, secrets, client)
        ids.append(A.draft("buy", f"Purchase order {po['po_id']} — {po['vendor']} — {po['units']:,} units, {_money(po['total'])}", text,
                           "approve_po", po, ref=ref, agent="Supplier buying", source=src))
    return ids


# ============================================================================ 5. data onboarding
def onboard_report(src, cfg=None):
    tmp = Path(tempfile.mkdtemp(prefix="stockflow_onb_"))
    try:
        return ingest.run(src, tmp, cfg=cfg, log=False, stale=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def mapping_guesses(sheet, unmapped, mapped):
    import difflib

    need = [c for c in ingest.std_cols(sheet) if c not in mapped.values() and c not in mapped]
    out = {}
    for std in need:
        cands = [ingest.key(std)] + [ingest.key(s) for s in ingest.SYN.get(std, [])]
        best = max(((difflib.SequenceMatcher(None, ingest.key(c), cd).ratio(), c) for c in unmapped for cd in cands), default=(0, None))
        if best[0] >= 0.6 and best[1] not in out.values():
            out[std] = best[1]
    return out


def onboard_text(rep):
    L, maps = [], []
    for f in rep["files"]:
        if f["status"] == "ok":
            req = [c for c in ingest.required(f["sheet"]) if c not in f["mapping"]]
            L.append(f"✓ {f['file']} read as {f['sheet']} ({f['rows_out']:,} of {f['rows_in']:,} rows kept)."
                     + (f" Missing required columns: {', '.join(req)}." if req else ""))
            guessed = [f"'{src}' → {std}" for std, src in f["mapping"].items() if ingest.key(src) != ingest.key(std)]
            if guessed:
                L.append("   Columns matched by name: " + ", ".join(guessed[:10]) + ". Check these are right.")
            g = mapping_guesses(f["sheet"], f.get("unmapped", []), {v: k for k, v in f["mapping"].items()}) if f.get("unmapped") else {}
            if g:
                L.append("   Suggested: " + "; ".join(f"'{s}' → {k}" for k, s in g.items()))
                maps.append((f["sheet"], {**f["mapping"], **g}))
            for n in f.get("notes", [])[:3]:
                L.append(f"   Note: {n}")
        else:
            L.append(f"✗ {f['file']}: {f['status']}. " + " ".join(f.get("notes", []))[:300])
    for m in rep["missing"]:
        L.append(f"Missing: no file was recognised as {m}. The plan cannot run without it.")
    for i in rep["issues"][:8]:
        L.append(f"Check: {i['title']}" + (f" — {i['detail']}" if i.get("detail") else ""))
    if not rep["missing"] and not rep["issues"]:
        L.append("Everything needed for a plan is here.")
    return "\n".join(L), maps


def run_onboard(src, user="agent", secrets=None, client=None, cfg=None, label="upload"):
    rep = onboard_report(src, cfg)
    fallback, maps = onboard_text(rep)
    facts = dict(files=rep["files"], missing=rep["missing"], issues=rep["issues"][:15])
    text, srcx = _write(facts, "Explain to a new customer, file by file, what StockFlow understood from their files, what is missing or "
                        "wrong, and exactly what to change. Suggest which unmapped columns probably mean which standard columns.",
                        fallback, secrets, client)
    ids = [A.draft("onboard", f"Onboarding check — {label} — {len(rep['files'])} files, {len(rep['missing'])} missing", text, None, {},
                   ref=f"onboard:{label}", agent="Data onboarding", source=srcx)]
    for sheet, m in maps:
        ids.append(A.draft("onboard", f"Save column mapping for {sheet}", "\n".join(f"{k} ← '{v}'" for k, v in m.items()),
                           "save_mapping", {"sheet": sheet, "mapping": m}, ref=f"map:{sheet}", agent="Data onboarding"))
    return ids, rep


# ============================================================================ 6. what-if planner
def parse_scenario(text, stores=(), categories=()):
    """Plain words -> scenario dict. Understands: close X; add a store like X (called Y); demand +20% [in category];
    cover days 21; approval value 50000."""
    t = str(text or "")
    low = t.lower()
    S = {s.upper(): s for s in stores}
    C = {c.lower(): c for c in categories}
    sc = dict(close=[], add=[], demand=[], settings={})
    for m in re.finditer(r"\b(?:close|shut|remove)\s+(?:store\s+)?([A-Za-z0-9-]+(?:\s*(?:,|and)\s*[A-Za-z0-9-]+)*)", t, re.I):
        for s in re.split(r"\s*(?:,|and)\s*", m.group(1)):
            if s.upper() in S:
                sc["close"].append(S[s.upper()])
    for m in re.finditer(r"\b(?:add|open)\s+(?:a\s+|one\s+|another\s+)?(?:new\s+)?store\s+like\s+([A-Za-z0-9-]+)(?:\s+(?:called|named|as)\s+([A-Za-z0-9-]+))?", t, re.I):
        if m.group(1).upper() in S:
            sc["add"].append(dict(like=S[m.group(1).upper()], id=(m.group(2) or f"NEW-{len(sc['add']) + 1}").upper()))
    for m in re.finditer(r"(?:demand|sales)\s*(?:goes\s*|rises\s*|falls\s*|drops\s*|up\s*|down\s*|by\s*)*([+-−]?\d+(?:\.\d+)?)\s*%(?:\s+(?:in|for)\s+([A-Za-z &-]+?))?(?=$|[,.;]|\s+and\b)", t, re.I):
        pct = float(m.group(1).replace("−", "-"))
        if re.search(r"(falls|drops|down)\s*(by\s*)?" + re.escape(m.group(1)), low):
            pct = -abs(pct)
        cat = (m.group(2) or "").strip().lower()
        sc["demand"].append(dict(pct=pct, category=C.get(cat) or next((v for k, v in C.items() if cat and cat in k), None)))
    m = re.search(r"cover\s*(?:days)?\s*(?:to|=|of)?\s*(\d+)", low)
    if m:
        sc["settings"]["planning.cover_days"] = int(m.group(1))
    m = re.search(r"approval\s*(?:value|limit)?\s*(?:to|=|of)?\s*₹?\s*([\d,]+)", low)
    if m:
        sc["settings"]["planning.approval_value"] = int(m.group(1).replace(",", ""))
    return sc


def describe(sc):
    b = [f"close {', '.join(sc['close'])}" for _ in [0] if sc["close"]]
    b += [f"add {a['id']} like {a['like']}" for a in sc["add"]]
    b += [f"demand {d['pct']:+g}%" + (f" in {d['category']}" if d.get("category") else "") for d in sc["demand"]]
    b += [f"{k.split('.')[-1].replace('_', ' ')} = {v}" for k, v in sc["settings"].items()]
    return "; ".join(b) or "no change"


def apply_scenario(std, sc, out):
    """Copy the standardised inputs in std to out with the scenario applied."""
    out = Path(out)
    shutil.copytree(std, out, dirs_exist_ok=True)
    rd = lambda n: pd.read_csv(out / n, dtype=str, keep_default_na=False) if (out / n).exists() else None  # noqa: E731
    st, wh, sa, sn, ln, it = rd("stores.csv"), rd("warehouses.csv"), rd("sales.csv"), rd("stock_snapshot.csv"), rd("lanes.csv"), rd("items.csv")
    for s in sc["close"]:                       # its stock goes back to the warehouse in its zone (or the first one)
        z = st.loc[st.store_id == s, "zone"].iloc[0] if "zone" in st and (st.store_id == s).any() else ""
        w = wh[wh.zone == z].warehouse_id.iloc[0] if "zone" in wh and (wh.zone == z).any() else wh.warehouse_id.iloc[0]
        st = st[st.store_id != s]
        sa = sa[sa.store_id != s]
        sn.loc[sn.location_id == s, "location_id"] = w
        if ln is not None:
            ln = ln[(ln["from"] != s) & (ln["to"] != s)]
    for a in sc["add"]:                         # a new store that sells like an existing one, with no stock yet
        row = st[st.store_id == a["like"]].copy()
        row["store_id"] = a["id"]
        st = pd.concat([st, row], ignore_index=True)
        sa = pd.concat([sa, sa[sa.store_id == a["like"]].assign(store_id=a["id"])], ignore_index=True)
        if ln is not None:
            ln = pd.concat([ln, ln[ln["from"] == a["like"]].assign(**{"from": a["id"]}), ln[ln["to"] == a["like"]].assign(to=a["id"])],
                           ignore_index=True)
    rng = np.random.default_rng(7)
    for d in sc["demand"]:                      # scale units sold; keep whole units without changing the total on average
        mask = pd.Series(True, index=sa.index)
        if d.get("category") and it is not None and "category" in it:
            mask = sa.sku_id.isin(set(it[it.category == d["category"]].sku_id))
        q = pd.to_numeric(sa.loc[mask, "qty"], errors="coerce").fillna(0) * (1 + d["pct"] / 100)
        fl = np.floor(q)
        sa.loc[mask, "qty"] = (fl + (rng.random(len(q)) < (q - fl))).astype(int).astype(str)
    for n, df in (("stores.csv", st), ("sales.csv", sa), ("stock_snapshot.csv", sn), ("lanes.csv", ln)):
        if df is not None:
            df.to_csv(out / n, index=False)


def _cfg_with(cfg, settings):
    import copy
    c = copy.deepcopy(cfg)
    for k, v in settings.items():
        a, b = k.split(".")
        c.setdefault(a, {})[b] = v
    return c


def _summary(R):
    k, _ = kpi.today(R)
    O = R["opportunity"]
    return dict(trips=len(R["trips"]), pieces=int(R["moves"].qty.sum()), net_benefit=float(O["net"]), move_cost=float(O["move_cost"]),
                in_stock_after=k["in_stock_after"], at_risk_after=k["at_risk_after"], cover_days_after=k["cover_days_after"],
                stock_units=k["stock_units"], pred7=k["pred7"], rules=f"{int((R['checks'].status == 'PASS').sum())}/{len(R['checks'])}")


def compare_text(sc, a, b):
    def f(k, x):
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "—"
        return f"{x:.1%}" if k == "in_stock_after" else _money(x) if k in ("net_benefit", "move_cost") else f"{x:,.1f}" if isinstance(x, float) else f"{x:,}" if isinstance(x, int) else str(x)
    names = dict(trips="Trips", pieces="Pieces moved", net_benefit="Expected benefit", move_cost="Moving cost",
                 in_stock_after="In stock after plan", at_risk_after="Items at risk after plan", cover_days_after="Days of cover after plan",
                 stock_units="Stock units", pred7="Forecast units next 7 days", rules="Checks passed")
    L = [f"What if: {describe(sc)}", "", f"{'':28}{'Today':>16}{'Scenario':>16}"]
    for k, n in names.items():
        L.append(f"{n:28}{f(k, a.get(k)):>16}{f(k, b.get(k)):>16}")
    return "\n".join(L)


def run_whatif(text, user="agent", plan_id=None, secrets=None, client=None, cfg=None, std=None):
    """Runs the planner twice (as is, and with the scenario) and drafts the comparison. Returns (draft id, scenario, base, new)."""
    p = plans.get(plan_id) if plan_id else _latest_plan()
    cfg = cfg or schema.load_config(extra=p.settings if p is not None and isinstance(p.settings, dict) else None)
    own = std is None
    if own:
        std = pipeline.std_inputs(p.plan_id, cfg) if p is not None else None
        if std is None:
            raise ValueError("This plan's input files are not stored, so it cannot be re-run. Build a plan from uploaded files first.")
    std = Path(std)
    work = Path(tempfile.mkdtemp(prefix="stockflow_whatif_"))
    try:
        st = pd.read_csv(std / "stores.csv", dtype=str, keep_default_na=False)
        it = pd.read_csv(std / "items.csv", dtype=str, keep_default_na=False)
        cats = sorted(set(it.category)) if "category" in it else []
        sc = parse_scenario(text, st.store_id.tolist(), cats)
        ai, key = _use_ai(secrets, client)
        if ai:
            try:
                j = A.ask_json(STYLE + " Turn a what-if question into a scenario. Return {\"close\": [store ids], \"add\": [{\"like\": store id, "
                               "\"id\": new id}], \"demand\": [{\"pct\": number, \"category\": name|null}], \"settings\": {\"planning.cover_days\": int, "
                               "\"planning.approval_value\": int}} using only these stores and categories.",
                               f"Question: {text}\nStores: {st.store_id.tolist()}\nCategories: {cats}", key=key, client=client)
                ok = set(st.store_id)
                j_sc = dict(close=[s for s in j.get("close", []) if s in ok], add=[a for a in j.get("add", []) if a.get("like") in ok],
                            demand=[d for d in j.get("demand", []) if isinstance(d.get("pct"), (int, float))],
                            settings={k: v for k, v in (j.get("settings") or {}).items() if k in ("planning.cover_days", "planning.approval_value")})
                for d in j_sc["demand"]:
                    d["category"] = d.get("category") if d.get("category") in cats else None
                if any(j_sc.values()):
                    sc = j_sc
            except Exception:  # noqa: BLE001
                pass
        if describe(sc) == "no change":
            return A.draft("whatif", f"What if: {text[:80]}", "I could not turn this into a change to test. Try: 'close KOL-SL', "
                           "'demand +20% in Dolls', 'add a store like PAT-01 called PAT-02', or 'cover days 21'.", None, {}, agent="What-if planner"), sc, {}, {}
        R0 = E.run(std, work / "base", cfg)
        apply_scenario(std, sc, work / "scen_in")
        R1 = E.run(work / "scen_in", work / "scen_out", _cfg_with(cfg, sc["settings"]))
        a, b = _summary(R0), _summary(R1)
        fallback = compare_text(sc, a, b)
        body, src = _write(dict(question=text, scenario=sc, today=a, scenario_result=b), "Explain this what-if result to a manager: "
                           "what changes, what it costs or earns, the risks, and a recommendation. Then show the key numbers.",
                           fallback, secrets, client)
        if src == "ai":
            body = body + "\n\n" + fallback
        did = A.draft("whatif", f"What if: {describe(sc)}"[:300], body, None, dict(scenario=sc, today=a, scenario_result=b),
                      ref=f"whatif:{describe(sc)}"[:120], agent="What-if planner", source=src)
        return did, sc, a, b
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if own:
            shutil.rmtree(std, ignore_errors=True)


AGENTS = {
    "brief": ("Morning brief", "Each morning: what needs approving, what is late, open problems and buys — per region.", "view"),
    "fix": ("Exception investigator", "Looks into each open problem, finds the likely cause and drafts a resolution.", "resolve"),
    "chase": ("Transfer follow-up", "Drafts WhatsApp nudges for late transfers and turns replies into updates.", "track_all"),
    "buy": ("Supplier buying", "Turns buy suggestions into purchase orders by supplier, with the email.", "settings"),
    "onboard": ("Data onboarding", "Checks a customer's files and explains what to fix; suggests column mappings.", "settings"),
    "whatif": ("What-if planner", "Ask 'what if we close a store / demand rises 20%' and get a side-by-side comparison.", "approve"),
}


def run_all(user="agent", secrets=None, client=None):
    """The daily agent round (brief, fixer, chaser, buyer). Returns {agent: draft ids}."""
    out = {}
    for k, fn in (("brief", run_brief), ("fix", run_fixer), ("chase", run_chaser), ("buy", run_buyer)):
        try:
            out[k] = fn(user=user, secrets=secrets, client=client)
        except Exception as e:  # noqa: BLE001
            out[k] = f"error: {type(e).__name__}: {e}"
    return out
