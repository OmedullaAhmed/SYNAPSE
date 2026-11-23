"""Monthly AI spending cap, shared by every AI feature (agents, Ask answers, reading photos, growth messages).

Before each call the worst case (estimated input + the full output allowance) is reserved against this month's cap;
after the call the reservation is replaced by the real cost from the response's token counts. A call that could take
the month over the cap is not made: the feature falls back to its rules / quick answers instead. Default cap ₹500 a
month (setting "ai_budget"), counted in Asia/Kolkata calendar months.

Also set a spend limit in the Claude Console as a second safety net: this cap counts list prices and cannot see usage
of the same API key by other apps.
"""
import json
import threading
from datetime import datetime

import db

# USD per million tokens (input, output), list prices Nov 2026. Unknown models are priced as the most expensive known one.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
          "claude-fable-5-2": (10.0, 50.0)}
DEFAULT = dict(cap_inr=500.0, inr_per_usd=88.0)
IMAGE_TOKENS = 1600
_LOCK = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def _month():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m")
    except Exception:  # noqa: BLE001
        return datetime.now().strftime("%Y-%m")


def conf():
    c = dict(DEFAULT)
    try:
        c.update({k: float(v) for k, v in (db.get_setting("ai_budget", {}) or {}).items() if k in DEFAULT})
    except Exception:  # noqa: BLE001
        pass
    return c


def set_cap(cap_inr, user, inr_per_usd=None):
    c = conf()
    c["cap_inr"] = max(0.0, float(cap_inr))
    if inr_per_usd:
        c["inr_per_usd"] = float(inr_per_usd)
    db.upsert_setting("ai_budget", c, user)


def spent(month=None):
    """dict(inr, calls, blocked, by_feature) for the month."""
    s = db.get_setting(f"ai_spend:{month or _month()}", None) or {}
    return dict(inr=float(s.get("inr", 0)), calls=int(s.get("calls", 0)), blocked=int(s.get("blocked", 0)),
                by_feature=dict(s.get("by_feature", {})))


def _add(inr=0.0, calls=0, blocked=0, feature="", user="ai"):
    m = _month()
    s = spent(m)
    s["inr"] = round(s["inr"] + inr, 4)
    s["calls"] += calls
    s["blocked"] += blocked
    if feature and inr:
        s["by_feature"][feature] = round(s["by_feature"].get(feature, 0) + inr, 4)
    db.upsert_setting(f"ai_spend:{m}", s, user)
    return s


def price(model):
    return PRICES.get(model) or max(PRICES.values())


def estimate_tokens(kw):
    n = 0

    def walk(x):
        nonlocal n
        if isinstance(x, dict):
            if x.get("type") == "image":
                n += IMAGE_TOKENS * 3            # counted in characters below (÷3)
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            n += len(x)
        elif x is not None and not isinstance(x, (int, float, bool)):
            walk(getattr(x, "__dict__", str(x)))
    walk([kw.get("system"), kw.get("messages"), kw.get("tools")])
    return n // 3 + 50                          # ~3 characters a token, rounded up a little


def cost_inr(model, tokens_in, tokens_out, c=None):
    c = c or conf()
    p_in, p_out = price(model)
    return (tokens_in * p_in + tokens_out * p_out) / 1e6 * c["inr_per_usd"]


def create(client, feature="ai", **kw):
    """client.messages.create(**kw) inside the monthly cap. Raises BudgetExceeded instead of overspending."""
    c = conf()
    model = kw.get("model", "")
    worst = cost_inr(model, estimate_tokens(kw), int(kw.get("max_tokens", 4000)), c)
    with _LOCK:
        now = spent()["inr"]
        if now + worst > c["cap_inr"]:
            _add(blocked=1)
            raise BudgetExceeded(f"Monthly AI budget reached (₹{now:,.0f} of ₹{c['cap_inr']:,.0f} used); using rules instead.")
        _add(worst, feature=feature)            # reserve the worst case first
    try:
        resp = client.messages.create(**kw)
    except Exception:
        with _LOCK:
            _add(-worst, feature=feature)
        raise
    u = getattr(resp, "usage", None)
    if u is not None and getattr(u, "input_tokens", None) is not None:
        tin = int(u.input_tokens or 0) + int(getattr(u, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + int(getattr(u, "cache_read_input_tokens", 0) or 0) * 0.1
        actual = cost_inr(model, tin, int(u.output_tokens or 0), c)
    else:
        actual = worst                          # no usage reported: keep the worst case
    with _LOCK:
        _add(actual - worst, calls=1, feature=feature)
    return resp


def summary():
    c, s = conf(), spent()
    return dict(month=_month(), cap_inr=c["cap_inr"], spent_inr=s["inr"], left_inr=max(0.0, c["cap_inr"] - s["inr"]), calls=s["calls"],
                blocked=s["blocked"], by_feature=s["by_feature"], inr_per_usd=c["inr_per_usd"])


def dumps():
    return json.dumps(summary(), indent=1)
