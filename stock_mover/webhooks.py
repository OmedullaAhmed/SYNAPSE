"""Outbound webhooks: tell other systems when something happens.

Events: plan.ready, trips.released, transfer.dispatched, transfer.received, exception.opened, count.saved.
Subscriptions are set by an admin (Integrations tab or POST /api/v1/webhooks): url, events, and a secret. Each delivery is
a JSON POST {"event", "at", "data"} signed with  X-StockFlow-Signature: sha256=<hex HMAC of the body with the secret>,
retried 3 times with back-off, and logged in webhook_log (never the secret).
"""
import hashlib
import hmac
import json
import threading
import time
import urllib.request
from datetime import datetime

import db

EVENTS = ["plan.ready", "trips.released", "transfer.dispatched", "transfer.received", "exception.opened", "count.saved"]


def subscriptions():
    try:
        return list(db.get_setting("webhooks", []) or [])
    except Exception:  # noqa: BLE001
        return []


def save_subscriptions(subs, user=""):
    clean = []
    for s in subs:
        url = str(s.get("url", "")).strip()
        if not url.startswith("https://") and not url.startswith("http://localhost") and not url.startswith("http://127.0.0.1"):
            raise ValueError(f"Webhook URLs must use https: {url[:60]}")
        ev = [e for e in s.get("events", []) if e in EVENTS] or list(EVENTS)
        clean.append(dict(url=url, events=ev, secret=str(s.get("secret", "")), active=bool(s.get("active", True))))
    db.upsert_setting("webhooks", clean, user)
    return clean


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _deliver(sub, event, body):
    err, code = "", 0
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(sub["url"], data=body, method="POST", headers={
                "Content-Type": "application/json", "User-Agent": "StockFlow-Webhook/1", "X-StockFlow-Event": event,
                **({"X-StockFlow-Signature": sign(sub["secret"], body)} if sub.get("secret") else {})})
            with urllib.request.urlopen(req, timeout=10) as r:   # noqa: S310 - admin-configured URL
                code = r.status
            if code < 300:
                _log(event, sub["url"], "ok", code, "", attempt)
                return True
            err = f"HTTP {code}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:500]
        time.sleep(min(2 ** attempt, 8))
    _log(event, sub["url"], "failed", code, err, 3)
    return False


def _log(event, url, status, code, error, attempts):
    try:
        db.insert_rows("webhook_log", [dict(at=datetime.now(), event=event, url=url[:300], status=status, code=int(code or 0),
                                            error=error, attempts=attempts)])
    except Exception:  # noqa: BLE001
        pass


def emit(event, data, wait=False):
    """Send an event to every active subscription that wants it. Background threads; never raises."""
    subs = [s for s in subscriptions() if s.get("active", True) and event in s.get("events", EVENTS)]
    if not subs:
        return 0
    body = json.dumps({"event": event, "at": datetime.now().isoformat(timespec="seconds"), "data": data}, default=str).encode()
    th = [threading.Thread(target=_deliver, args=(s, event, body), daemon=True) for s in subs]
    for t in th:
        t.start()
    if wait:
        for t in th:
            t.join(60)
    return len(subs)


def log(limit=200):
    try:
        return db.read("webhook_log", order=["-at"], limit=limit)
    except Exception:  # noqa: BLE001
        return None
