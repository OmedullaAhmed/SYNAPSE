"""Audit trail: who did what, when. Every write goes through log(); nothing in the app edits or deletes audit rows."""
from datetime import datetime

import pandas as pd

import db

ACTIONS = {
    "login.ok": "Signed in", "login.guest": "Opened demo as guest", "user.bulk": "Imported users", "accuracy.run": "Measured forecast accuracy", "apikey.create": "Created API key", "apikey.revoke": "Revoked API key", "webhooks.set": "Changed webhooks", "count.save": "Saved a stock count", "vision.read": "Read stock from images", "ai.vision": "Changed AI image reading", "login.sso": "Signed in with company account", "queue.decide": "Regional queue decision", "exception.resolve": "Resolved exception",
    "exception.assign": "Assigned exception", "mapping.save": "Saved column mapping", "automation.settings": "Changed automation",
    "automation.run": "Daily run", "automation.failed": "Daily run failed", "erp.export": "Sent orders to ERP", "erp.poll": "Picked up ERP confirmations", "login.failed": "Failed sign-in", "login.locked": "Account locked", "logout": "Signed out",
    "session.timeout": "Session timed out", "user.create": "Created user", "user.update": "Changed user", "user.password": "Changed password",
    "plan.build": "Built a plan", "plan.reopen": "Reopened a plan", "decision.line": "Line decision", "decision.bulk": "Bulk line decision",
    "orders.release": "Released transfer orders", "orders.download": "Downloaded transfer orders", "move.dispatched": "Marked as left",
    "move.received": "Marked as received", "move.upload": "Uploaded movements", "settings.accept": "Accepted suggested settings",
    "settings.reset": "Reset settings", "whatsapp.send": "Sent WhatsApp alerts", "ai.enable": "Turned on AI answers",
    "feedback.rate": "Rated a plan",
}


def log(action, obj="", details=None, user="", role=""):
    try:
        db.insert_rows("audit", [dict(at=datetime.now(), user=user, role=role, action=action, object=str(obj)[:200],
                                      details=details or {})])
    except Exception:  # noqa: BLE001  (auditing must never break the user's action; the error is visible in server logs)
        import traceback
        traceback.print_exc()


def read(limit=5000):
    d = db.read("audit", order=["-id"], limit=limit)
    if len(d):
        d["what"] = d.action.map(ACTIONS).fillna(d.action)
        d["details"] = d.details.map(lambda x: ", ".join(f"{k}={v}" for k, v in (x or {}).items()) if isinstance(x, dict) else str(x or ""))
    return d


def as_frame(limit=5000):
    d = read(limit)
    return d[["at", "user", "role", "what", "object", "details", "action"]] if len(d) else pd.DataFrame()
