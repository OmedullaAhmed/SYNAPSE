"""WhatsApp alerts when trips are approved and released.

Who gets what
  area managers  (managers.whatsapp)   : stock arriving at their stores, and what their stores must pack today
  store contacts (stores.whatsapp)     : the same, for one store
  warehouses     (warehouses.whatsapp) : trips to pick and pack today

How it is sent
  1. Automatic, through the WhatsApp Business Platform, when Streamlit secrets hold a [whatsapp] section:

       [whatsapp]
       provider = "meta"                  # Meta WhatsApp Cloud API
       token = "EAAG..."                  # permanent system-user token
       phone_number_id = "1234567890"
       template = "stockflow_approved"    # approved utility template with 2 body variables: {{1}} name, {{2}} summary
       language = "en"

       # or
       provider = "twilio"
       account_sid = "AC..."
       auth_token = "..."
       from = "whatsapp:+14155238886"
       content_sid = "HX..."              # approved template with variables 1 = name, 2 = summary (optional: plain text otherwise)

     Business-initiated WhatsApp messages need an approved template; plain text only reaches people who
     messaged the business number in the last 24 hours.
  2. Otherwise: a "Send on WhatsApp" button per person that opens WhatsApp with the message ready to send.

Phone numbers are staff personal data: they are used only to send these alerts, never shown to the AI assistant,
and the send log keeps only the last 4 digits.
"""
import json
import re
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime

import pandas as pd

import feedback as fb

LOG = fb.DIR / "whatsapp_log.csv"


def phone(raw, default_cc="91"):
    """E.164 digits without '+', or '' if it doesn't look like a mobile number."""
    d = re.sub(r"\D", "", str(raw or ""))
    if not d:
        return ""
    if len(d) == 10:
        d = default_cc + d
    elif d.startswith("0") and len(d) == 11:
        d = default_cc + d[1:]
    return d if 10 <= len(d) <= 15 else ""


def _summary_for_stores(stores, orders, trips, R):
    """One line (no line breaks: WhatsApp template variables can't hold them)."""
    t = trips[trips.to_number.isin(set(orders.to_number))]
    inn, out = t[t.to_id.isin(stores)], t[t.from_id.isin(stores)]
    parts = []
    for s, grp in inn.groupby("to_id"):
        urg = int(grp.urgent.gt(0).sum())
        parts.append(f"{s} receives {int(grp.pieces.sum())} pcs on {len(grp)} trip{'s' if len(grp) > 1 else ''}"
                     + (f" ({urg} urgent)" if urg else "") + f" by {grp.arrives.max():%d %b}")
    for s, grp in out.groupby("from_id"):
        parts.append(f"{s} packs {int(grp.pieces.sum())} pcs today ({', '.join(grp.to_number.head(3))}"
                     + (" …" if len(grp) > 3 else "") + ")")
    return "; ".join(parts)


def recipients(R, orders):
    """Messages for everyone with a WhatsApp number who is affected by the released orders."""
    pl, trips, cfg = R["pl"], R["trips"], R["cfg"]
    cc = str(cfg.get("whatsapp", {}).get("default_country_code", "91"))
    brand = cfg["brand"]["name"]
    head = f"StockFlow · {brand}: plan for {pl.run:%d %b} approved."
    tail = "Open StockFlow for the size-by-size list."
    out = []
    if orders is None or len(orders) == 0:
        return out
    mgr = R["P"]["mgr"]
    if len(mgr) and "whatsapp" in mgr:
        for mid, m in mgr.iterrows():
            ph = phone(m.get("whatsapp"), cc)
            ss = [x.strip() for x in str(m.get("stores", "")).split(",") if x.strip()]
            summ = _summary_for_stores(ss, orders, trips, R)
            if ph and summ:
                name = str(m.get("name", mid))
                out.append(dict(who=name, role="Area manager", phone=ph, summary=summ,
                                text=f"{head} Hi {name}: {summ}. {tail}"))
    st = pl.st
    if "whatsapp" in st:
        for s, row in st.iterrows():
            ph = phone(row.get("whatsapp"), cc)
            summ = _summary_for_stores([s], orders, trips, R)
            if ph and summ:
                out.append(dict(who=f"{s} store", role="Store", phone=ph, summary=summ, text=f"{head} {summ}. {tail}"))
    whs = R["P"]["whs"]
    if "whatsapp" in whs:
        t = trips[trips.to_number.isin(set(orders.to_number))]
        for w, row in whs.iterrows():
            ph = phone(row.get("whatsapp"), cc)
            mine = t[t.from_id == w]
            if ph and len(mine):
                summ = (f"{w} picks and packs {int(mine.pieces.sum())} pcs on {len(mine)} trips today "
                        f"to {', '.join(sorted(mine.to_id.unique()))}")
                out.append(dict(who=f"{w} warehouse", role="Warehouse", phone=ph, summary=summ, text=f"{head} {summ}. {tail}"))
    return out


def wa_link(ph, text):
    return f"https://wa.me/{ph}?text={urllib.parse.quote(text)}"


def configured(secrets):
    try:
        w = dict(secrets["whatsapp"]) if secrets is not None and "whatsapp" in secrets else {}
    except Exception:  # noqa: BLE001  (no secrets file)
        w = {}
    p = w.get("provider", "").lower()
    if p == "meta" and w.get("token") and w.get("phone_number_id"):
        return w
    if p == "twilio" and w.get("account_sid") and w.get("auth_token") and w.get("from"):
        return w
    return {}


def _post(url, data, headers, timeout=15):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf8", "ignore")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def build_request(w, rec):
    """(url, body bytes, headers) for one message. Kept separate so it can be checked without sending."""
    if w["provider"].lower() == "meta":
        url = f"https://graph.facebook.com/v21.0/{w['phone_number_id']}/messages"
        if w.get("template"):
            msg = {"messaging_product": "whatsapp", "to": rec["phone"], "type": "template",
                   "template": {"name": w["template"], "language": {"code": w.get("language", "en")},
                                "components": [{"type": "body", "parameters": [
                                    {"type": "text", "text": rec["who"][:60]}, {"type": "text", "text": rec["summary"][:900]}]}]}}
        else:
            msg = {"messaging_product": "whatsapp", "to": rec["phone"], "type": "text", "text": {"body": rec["text"][:4000]}}
        return url, json.dumps(msg).encode(), {"Authorization": f"Bearer {w['token']}", "Content-Type": "application/json"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{w['account_sid']}/Messages.json"
    form = {"From": w["from"] if w["from"].startswith("whatsapp:") else f"whatsapp:{w['from']}", "To": f"whatsapp:+{rec['phone']}"}
    if w.get("content_sid"):
        form["ContentSid"] = w["content_sid"]
        form["ContentVariables"] = json.dumps({"1": rec["who"][:60], "2": rec["summary"][:900]})
    else:
        form["Body"] = rec["text"][:1500]
    auth = b64encode(f"{w['account_sid']}:{w['auth_token']}".encode()).decode()
    return url, urllib.parse.urlencode(form).encode(), {"Authorization": f"Basic {auth}",
                                                         "Content-Type": "application/x-www-form-urlencoded"}


def send_all(w, recs, user, sender=_post):
    rows = []
    for rec in recs:
        url, body, headers = build_request(w, rec)
        code, resp = sender(url, body, headers)
        ok = 200 <= code < 300
        err = ""
        if not ok:
            try:
                j = json.loads(resp)
                err = (j.get("error", {}) or {}).get("message") or j.get("message") or resp[:160]
            except ValueError:
                err = resp[:160]
        rows.append(dict(sent_at=datetime.now().strftime("%Y-%m-%d %H:%M"), by=user, to=rec["who"], role=rec["role"],
                         number=f"…{rec['phone'][-4:]}", provider=w["provider"], status="sent" if ok else "failed", error=err))
    if rows:
        fb._append(LOG, rows)
    return pd.DataFrame(rows)


def log():
    return pd.read_csv(LOG, dtype=str) if LOG.exists() else pd.DataFrame()



def movement_recipients(R, t, kind):
    """Who to tell when a transfer order leaves (kind='left') or is received short/over (kind='received').
    t: one row of feedback.tracking() orders."""
    cfg, pl = R["cfg"], R["pl"]
    cc = str(cfg.get("whatsapp", {}).get("default_country_code", "91"))
    head = f"StockFlow · {cfg['brand']['name']}:"
    if kind == "left":
        msg = (f"{head} {t.to_number} left {t.from_location} with {int(t.sent or t.planned)} pcs for {t.to_location}, "
               f"expected by {pd.Timestamp(t.expected):%d %b}. Please mark it received in StockFlow when it arrives.")
        targets = {t.to_location}
    else:
        base = int(t.sent or t.planned)
        diff = int(t.received) - base
        msg = (f"{head} {t.to_number} from {t.from_location} was received at {t.to_location}: {int(t.received)} of {base} pcs"
               + (f" ({-diff} short)" if diff < 0 else f" ({diff} extra)" if diff > 0 else "") + ". Please check the count.")
        targets = {t.to_location, t.from_location}
    out, seen = [], set()

    def add(who, role, raw):
        ph = phone(raw, cc)
        if ph and ph not in seen:
            seen.add(ph)
            out.append(dict(who=who, role=role, phone=ph, summary=msg[len(head) + 1:], text=msg))
    st = pl.st
    for loc in targets:
        if "whatsapp" in st and loc in st.index:
            add(f"{loc} store", "Store", st.at[loc, "whatsapp"])
        whs = R["P"]["whs"]
        if "whatsapp" in whs and loc in whs.index:
            add(f"{loc} warehouse", "Warehouse", whs.at[loc, "whatsapp"])
    mgr = R["P"]["mgr"]
    if len(mgr) and "whatsapp" in mgr:
        for mid, m in mgr.iterrows():
            if targets & {x.strip() for x in str(m.get("stores", "")).split(",")}:
                add(str(m.get("name", mid)), "Area manager", m.get("whatsapp"))
    return out
