"""Marketing agent for the people selling StockFlow: prospect brief, ROI estimate, tailored messages, follow-up sequence,
a personalised demo data pack, and a simple lead tracker.

It drafts; you send. Nothing is emailed or messaged automatically. Use business contacts only (a work email or a number
the business publishes), keep the opt-out line, and stop when someone asks you to (India's DPDP Act, anti-spam rules).

With an ANTHROPIC_API_KEY, Claude personalises the messages from notes you paste (their website's About page, a news
item, what they said on a call). Without it, the same pack comes from templates.
"""
import json
import os
import re
from datetime import date, datetime, timedelta

import db
import demo_factory as F

STAGES = ["New", "Contacted", "Replied", "Demo booked", "Demo done", "Pilot", "Won", "Lost", "Not now"]
PAINS = {
    "stockouts": "best sellers run out in some stores while other stores hold spare stock",
    "dead_stock": "slow and old stock sits in the wrong stores until it is marked down",
    "excel": "transfers are planned in spreadsheets and WhatsApp, every day, by a few people",
    "visibility": "nobody sees stock across all warehouses and stores in one place",
    "transfers": "transfers leave late, arrive short, and nobody follows up",
    "buying": "buying is done by gut feel, without knowing what can be moved instead",
    "expiry": "stock close to expiry is found too late",
}
OPT_OUT = "If this is not relevant, reply 'no' and I won't contact you again."


# ----------------------------------------------------------------------------- ROI
def roi(revenue_cr, stores, warehouses, margin=0.35, stock_days=90, in_stock_gain=0.03, lost_share=0.5, old_share=0.2,
        old_cut=0.15, markdown=0.3, stock_cut=0.05, carry=0.12, hours_per_store_week=1.0, hour_cost=300, price_store_month=1500,
        price_wh_month=3000):
    """Yearly benefit estimate in ₹ with every assumption visible. Conservative = half of every gain."""
    rev = float(revenue_cr) * 1e7
    stock = rev * (1 - margin) * stock_days / 365                       # stock at cost
    parts = {
        "Sales recovered from fewer stock-outs": rev * in_stock_gain * lost_share * margin,
        "Less markdown on old stock": stock * old_share * old_cut * markdown,
        "Lower stock holding cost": stock * stock_cut * carry,
        "Planning time saved": stores * hours_per_store_week * 52 * hour_cost,
    }
    expected = sum(parts.values())
    cost = 12 * (stores * price_store_month + warehouses * price_wh_month)
    return dict(parts=parts, expected=expected, conservative=expected / 2, cost=cost, stock=stock,
                payback_months=(cost / (expected / 2) * 12) if expected > 0 else None,
                assumptions=dict(gross_margin=margin, stock_days=stock_days, in_stock_gain=in_stock_gain, lost_sale_share=lost_share,
                                 old_stock_share=old_share, old_stock_cut=old_cut, markdown_rate=markdown, stock_cut=stock_cut,
                                 carrying_cost=carry, hours_per_store_week=hours_per_store_week, hour_cost=hour_cost,
                                 price_store_month=price_store_month, price_wh_month=price_wh_month))


def money(x):
    x = float(x or 0)
    return f"₹{x / 1e7:,.2f} cr" if abs(x) >= 1e7 else f"₹{x / 1e5:,.1f} lakh" if abs(x) >= 1e5 else f"₹{x:,.0f}"


def roi_text(r):
    L = [f"Estimated yearly benefit: {money(r['conservative'])} (conservative) to {money(r['expected'])} (expected)."]
    L += [f"  • {k}: {money(v)}" for k, v in r["parts"].items()]
    L.append(f"StockFlow at the assumed price: {money(r['cost'])} a year"
             + (f"; pays back in about {r['payback_months']:.0f} months on the conservative case." if r["payback_months"] else "."))
    a = r["assumptions"]
    L.append(f"Assumptions: margin {a['gross_margin']:.0%}, {a['stock_days']} days of stock, in-stock +{a['in_stock_gain'] * 100:.0f} points, "
             f"{a['lost_sale_share']:.0%} of out-of-stock demand lost, old stock {a['old_stock_share']:.0%} of stock cut by "
             f"{a['old_stock_cut']:.0%}, markdown {a['markdown_rate']:.0%}. These are estimates to check in a pilot, not promises.")
    return "\n".join(L)


# ----------------------------------------------------------------------------- messages
def _lead(L):
    L = dict(L)
    L.setdefault("contact", "")
    L["first"] = (L.get("contact") or "").split(" ")[0] or "there"
    L["cat_label"] = F.PRESETS.get(L.get("category") or F.category_of(L.get("category")), F.PRESETS["general"])["label"].lower()
    L["pain_text"] = "; ".join(PAINS[p] for p in (L.get("pains") or [])[:2] if p in PAINS) or PAINS["stockouts"]
    return L


def templates(L, r=None, sender="", product="StockFlow"):
    L = _lead(L)
    w, s = int(L.get("warehouses") or 1), int(L.get("stores") or 2)
    benefit = f" For a business your size we estimate {money(r['conservative'])}–{money(r['expected'])} a year." if r else ""
    sig = f"\n\n{sender}" if sender else ""
    email = (f"Subject: {L['company']}: the right stock in the right store, every morning\n\n"
             f"Hi {L['first']},\n\n"
             f"Most {L['cat_label']} businesses with {w} warehouse{'s' if w > 1 else ''} and {s} stores tell us the same thing: {L['pain_text']}.\n\n"
             f"{product} reads the sales and stock files you already have (any POS or ERP export) and each morning proposes which "
             f"stock to move between your warehouses and stores, what to buy, and what to pull back, with the reason for every line. "
             f"Managers approve on their phone; dispatch and receipt are tracked to the piece.{benefit}\n\n"
             f"I can show it on a demo built like {L['company']} ({w} warehouses, {s} stores) in 20 minutes. Would Tuesday or Thursday "
             f"work?\n\n{OPT_OUT}{sig}")
    wa = (f"Hi {L['first']}, this is {sender or 'the StockFlow team'}. We help {L['cat_label']} businesses with several stores move the right "
          f"stock to the right store every morning (from your existing POS/ERP files). Could I show you a 20-minute demo set up like "
          f"{L['company']}? {OPT_OUT}")
    li = (f"Hi {L['first']}, I work on stock planning for multi-store {L['cat_label']} businesses (fewer stock-outs, less dead stock). "
          f"Would be glad to connect.")[:300]
    call = (f"Opening: 'Hi {L['first']}, quick one: when a best seller runs out at one {L['company']} store, how do you find out, and how "
            f"long until stock arrives from another store or the warehouse?'\nListen for: spreadsheets, WhatsApp groups, weekly (not daily) "
            f"planning, no shared view of stock.\nOffer: 'Send me last month's sales and today's stock export, any format, and I'll show "
            f"you tomorrow's transfer plan for your own stores.'\nClose: book the demo; ask who else decides (owner, operations head, IT).")
    return dict(email=email, whatsapp=wa, linkedin=li, call=call)


def followups(L, sender=""):
    L = _lead(L)
    sig = f"\n\n{sender}" if sender else ""
    return [
        (3, f"Subject: Re: {L['company']} stock planning\n\nHi {L['first']}, in case this got buried: happy to show a demo built like "
            f"{L['company']}. It takes 20 minutes and needs nothing from you.\n\n{OPT_OUT}{sig}"),
        (7, f"Subject: What one morning plan looks like\n\nHi {L['first']}, here is what a {L['cat_label']} chain typically sees on day one: "
            f"which stores are about to run out, which hold spare stock of the same items, and a short list of transfers to fix it. "
            f"I've attached a sample. Worth a look?\n\n{OPT_OUT}{sig}"),
        (14, f"Subject: A free check of your own data\n\nHi {L['first']}, if you send one month of sales and today's stock (any export), "
             f"I'll send back the transfer and buying plan StockFlow would make for {L['company']}, with no commitment.\n\n{OPT_OUT}{sig}"),
        (30, f"Subject: Closing the loop\n\nHi {L['first']}, I won't keep writing. If stock planning becomes a priority, reply to this email "
             f"and I'll set up the demo.{sig}"),
    ]


def brief(L, r=None):
    L = _lead(L)
    w, s = int(L.get("warehouses") or 1), int(L.get("stores") or 2)
    pains = [PAINS[p] for p in (L.get("pains") or []) if p in PAINS]
    return "\n".join([
        f"{L['company']} — {L['cat_label']}, {w} warehouse(s), {s} store(s)" + (f", {L['city']}" if L.get("city") else "")
        + (f", about ₹{float(L['revenue_cr']):g} cr a year" if L.get("revenue_cr") else ""),
        f"Contact: {L.get('contact') or '—'}" + (f" ({L['role']})" if L.get("role") else ""),
        "Likely problems: " + ("; ".join(pains) if pains else "not known yet: ask on the first call."),
        "Who decides: owner or managing director; operations or supply-chain head; IT only for the file feed.",
        "What to show: their kind of day in the demo (" + F.story(L["company"], L.get("category") or "general", w, s) + ")",
        "Objections to expect: 'our data is messy' (it reads any export and lists what to fix), 'we already have an ERP' (it uses the ERP's "
        "files and sends orders back), 'staff won't use it' (approve on the phone, WhatsApp alerts), 'data security' (own server option, "
        "roles, audit trail, no AI unless switched on).",
        roi_text(r) if r else "",
    ]).strip()


def ai_pack(L, r, notes="", sender="", key=None, client=None):
    """Claude personalises the email, WhatsApp and LinkedIn note from pasted notes. Returns dict or None."""
    import agents as A
    key = key or os.environ.get("ANTHROPIC_API_KEY")
    if not key and client is None:
        return None
    system = ("You write B2B sales outreach for StockFlow, a stock planning app for businesses with several warehouses and stores (any POS/ERP "
              "export in; daily transfer, buying and pull-back plan out; phone approvals; dispatch/receipt tracking; WhatsApp alerts). "
              "Write like a helpful person, not an advertisement: short, specific to the prospect, no hype words, no invented facts or "
              "client names, no claims of results. Every email and WhatsApp ends with this opt-out line exactly: " + OPT_OUT)
    prompt = (f"Prospect: {json.dumps(_lead(L), default=str)}\nROI estimate (label it an estimate): conservative {money(r['conservative'])}, "
              f"expected {money(r['expected'])} a year.\nNotes about them (may be empty): {notes[:4000]}\nSender: {sender}\n"
              'Return {"email": "Subject: ...\\n\\n...", "whatsapp": "...", "linkedin": "max 300 characters", "angle": "one line: why now for them"}')
    try:
        j = A.ask_json(system, prompt, key=key, client=client)
    except Exception:  # noqa: BLE001
        return None
    out = {k: str(v) for k, v in j.items() if k in ("email", "whatsapp", "linkedin", "angle") and v}
    for k in ("email", "whatsapp"):
        if k in out and OPT_OUT not in out[k]:
            out[k] += "\n\n" + OPT_OUT
    if "linkedin" in out:
        out["linkedin"] = out["linkedin"][:300]
    return out or None


# ----------------------------------------------------------------------------- leads
FIELDS = ["company", "contact", "role", "email", "phone", "city", "category", "warehouses", "stores", "revenue_cr", "pains", "stage",
          "next_step", "next_date", "notes", "source", "owner"]


def leads():
    d = db.read("leads", order=["-updated_at"])
    return d


def save_lead(L, user=""):
    L = {k: L.get(k) for k in FIELDS if k in L}
    if L.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(L["email"])):
        raise ValueError("That email address does not look right.")
    L["company"] = str(L.get("company") or "").strip()[:200]
    if not L["company"]:
        raise ValueError("Company name is needed.")
    L.setdefault("stage", "New")
    now = datetime.now()
    cur = db.read("leads", {"company": L["company"]})
    if len(cur):
        db.update_rows("leads", {"id": int(cur.iloc[0].id)}, dict(L, updated_at=now))
        return int(cur.iloc[0].id)
    db.insert_rows("leads", [dict(L, created_at=now, updated_at=now, owner=L.get("owner") or user, assets={})])
    return int(db.read("leads", {"company": L["company"]}).iloc[0].id)


def set_stage(lead_id, stage, next_step="", next_date=None, user=""):
    if stage not in STAGES:
        raise ValueError(f"Stage must be one of {STAGES}")
    db.update_rows("leads", {"id": int(lead_id)}, dict(stage=stage, next_step=next_step[:300], next_date=next_date, updated_at=datetime.now()))


def save_assets(lead_id, assets):
    db.update_rows("leads", {"id": int(lead_id)}, dict(assets=assets, updated_at=datetime.now()))


def due(today=None):
    """Leads whose next step is due (today or overdue), excluding closed ones."""
    d = leads()
    if d.empty:
        return d
    today = today or date.today()
    nd = d.next_date.apply(lambda x: x if isinstance(x, date) else (date.fromisoformat(str(x)[:10]) if x and str(x)[:10] != "None" else None))
    return d[nd.notna() & (nd <= today) & ~d.stage.isin(["Won", "Lost", "Not now"])]


def pack(L, sender="", notes="", key=None, client=None, **roi_kw):
    """Everything for one prospect: brief, ROI, messages (AI-personalised when possible), follow-ups, demo zip bytes."""
    r = roi(float(L.get("revenue_cr") or 0) or max(1.0, int(L.get("stores") or 2) * 1.5), int(L.get("stores") or 2),
            int(L.get("warehouses") or 1), **roi_kw)
    msgs = templates(L, r, sender)
    ai = ai_pack(L, r, notes, sender, key, client)
    if ai:
        msgs.update({k: v for k, v in ai.items() if k != "angle"})
    start = date.today()
    seq = [dict(day=d, date=(start + timedelta(days=d)).isoformat(), text=t) for d, t in followups(L, sender)]
    demo = F.make(L["company"], L.get("category") or F.category_of(L.get("notes", "")), int(L.get("warehouses") or 1),
                  int(L.get("stores") or 2), [c for c in str(L.get("city") or "").split(",") if c.strip()] or None)
    return dict(brief=brief(L, r), roi=r, roi_text=roi_text(r), messages=msgs, angle=(ai or {}).get("angle", ""), followups=seq,
                demo_zip=demo, source="ai" if ai else "templates")
