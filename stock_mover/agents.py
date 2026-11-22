"""Agent layer: AI assistants that investigate and draft; people approve; approved actions run through the normal,
audited code paths.

Rules every agent follows (enforced here, not left to the model):
  - Agents only READ data and write DRAFTS (table agent_drafts). Nothing moves stock, money or messages until a person
    with the right role approves the draft.
  - On approval, only actions on the ACTIONS whitelist can run, each with its own role check and an audit entry.
  - With AI switched off (default) or no API key, every agent still works: drafts are written from rules and templates.
  - With AI switched on by an admin, Claude writes the drafts and may call read-only tools (max 8 steps per run).
"""
import contextlib
import json
import os
import re
import threading
from datetime import datetime

import pandas as pd

import audit
import db
import exceptions_queue as XQ
import feedback as fb
import ingest
import notify

MODEL = os.environ.get("STOCKFLOW_AGENT_MODEL", "claude-sonnet-5-5")
MAX_STEPS = 8
STATUSES = ("pending", "approved", "rejected", "done", "failed")


# ----------------------------------------------------------------------------- settings
_LOCAL = threading.local()


@contextlib.contextmanager
def rules_only():
    """Inside this block agents never call the AI (used for guest demos)."""
    old = getattr(_LOCAL, "off", False)
    _LOCAL.off = True
    try:
        yield
    finally:
        _LOCAL.off = old


def ai_status(secrets=None):
    """(enabled by an admin, API key available). Agents use AI only when both are true."""
    if getattr(_LOCAL, "off", False):
        return False, None
    try:
        on = bool((db.get_setting("ai_agents", {}) or {}).get("enabled"))
    except Exception:  # noqa: BLE001
        on = False
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and secrets is not None:
        try:
            key = secrets["ANTHROPIC_API_KEY"]
        except Exception:  # noqa: BLE001
            key = None
    return on, key


def set_ai(enabled, user):
    db.upsert_setting("ai_agents", dict(enabled=bool(enabled), by=user, at=datetime.now().isoformat(timespec="seconds")), user)
    audit.log("ai.agents", "on" if enabled else "off", {}, user, "admin")


# ----------------------------------------------------------------------------- drafts
def draft(kind, title, body, action=None, payload=None, region="", location="", ref="", agent="", source="rules"):
    """Store a draft for a person to review. action must be in ACTIONS (or None for information only)."""
    if action and action not in ACTIONS:
        raise ValueError(f"unknown action {action}")
    db.insert_rows("agent_drafts", [dict(kind=kind, created_at=datetime.now(), agent=agent or kind, source=source, status="pending",
                                         title=str(title)[:300], body=str(body)[:8000], action=action or "",
                                         payload=payload or {}, region=str(region)[:80], location=str(location)[:40],
                                         ref=str(ref)[:120], decided_by="", result="")])
    d = db.read("agent_drafts", order=["-id"], limit=1)
    return int(d.iloc[0].id)


def drafts(status=("pending",), kind=None):
    d = db.read("agent_drafts", order=["-id"])
    if d.empty:
        return d
    if status:
        d = d[d.status.isin(status)]
    if kind:
        d = d[d.kind.isin([kind] if isinstance(kind, str) else kind)]
    return d


def supersede(kind, ref):
    """Older pending drafts for the same thing are replaced by a new run."""
    d = drafts(("pending",), kind)
    for i in d[d.ref == ref].id if len(d) else []:
        db.update_rows("agent_drafts", {"id": int(i)}, dict(status="rejected", decided_by="agent", result="replaced by a newer draft"))


def decide(draft_id, user, role, approve, body=None, can=None, secrets=None):
    """Approve (runs the whitelisted action) or reject a draft. can(perm) -> bool is the caller's permission check."""
    d = db.read("agent_drafts", {"id": int(draft_id)})
    if d.empty:
        raise ValueError("No such draft.")
    r = d.iloc[0]
    if r.status != "pending":
        raise ValueError(f"This draft is already {r.status}.")
    if body is not None and body != r.body:
        db.update_rows("agent_drafts", {"id": int(draft_id)}, {"body": str(body)[:8000]})
        r = db.read("agent_drafts", {"id": int(draft_id)}).iloc[0]
    if not approve:
        db.update_rows("agent_drafts", {"id": int(draft_id)}, dict(status="rejected", decided_by=user, decided_at=datetime.now()))
        audit.log("agent.reject", str(draft_id), {"kind": r.kind, "title": r.title[:120]}, user, role)
        return "Rejected."
    msg, status = "Approved (information only).", "approved"
    if r.action:
        perm, fn = ACTIONS[r.action]
        if can is not None and not can(perm):
            raise PermissionError(f"Your role cannot do this ({perm}).")
        try:
            msg = fn(dict(r.payload or {}), r.body, user, secrets) or "Done."
            status = "done"
        except Exception as e:  # noqa: BLE001
            msg, status = f"Failed: {type(e).__name__}: {e}", "failed"
    db.update_rows("agent_drafts", {"id": int(draft_id)}, dict(status=status, decided_by=user, decided_at=datetime.now(), result=msg[:1000]))
    audit.log("agent.approve", str(draft_id), {"kind": r.kind, "action": r.action, "result": msg[:200]}, user, role)
    return msg


# ----------------------------------------------------------------------------- whitelisted actions (run only on approval)
def _resolve(p, body, user, secrets):
    XQ.resolve([int(i) for i in p["ids"]], user, p.get("note") or body[:1500])
    return f"Resolved {len(p['ids'])} exception(s)."


def _record(p, body, user, secrets):
    (fb.mark_dispatched if p["event"] == "dispatched" else fb.mark_received)(p["to_number"], user, p.get("lines"), p.get("note", "agent"))
    audit.log(f"move.{p['event']}", p["to_number"], {"via": "agent", "lines": len(p.get("lines") or {})}, user, "")
    XQ.sync_transfers(user)
    return f"{p['to_number']} marked {p['event']}."


def _send_whatsapp(p, body, user, secrets):
    w = notify.configured(secrets) if secrets is not None else None
    recs = [dict(who=p.get("who", "recipient"), role=p.get("role", ""), phone=p["phone"], summary=body[:900], text=body[:3900])]
    if not w:
        return "WhatsApp Business API is not set up: open this link to send it yourself: " + notify.wa_link(p["phone"], body)
    res = notify.send_all(w, recs, user)
    return f"WhatsApp to {p.get('who')}: {res.iloc[0].status}" if len(res) else "Not sent."


def _post_brief(p, body, user, secrets):
    url = notify.webhook_url(secrets)
    ok = notify.post_webhook(url, body) if url else False
    return "Posted to the team channel." if ok else "Saved (no team channel webhook set up)."


def _approve_po(p, body, user, secrets):
    db.upsert_setting(f"po:{p['po_id']}"[:40], dict(p, approved_by=user, approved_at=datetime.now().isoformat(timespec="seconds"),
                                                    email=body), user)
    return f"Purchase order {p['po_id']} approved for {p.get('vendor')}. Download it from the Agents tab and send it to the supplier."


def _save_mapping(p, body, user, secrets):
    ingest.save_profile(p["sheet"], p["mapping"], user)
    return f"Mapping for {p['sheet']} saved."


ACTIONS = {  # action: (permission needed, function)
    "resolve_exceptions": ("resolve", _resolve),
    "record_transfer": ("track_all", _record),
    "send_whatsapp": ("track_all", _send_whatsapp),
    "post_brief": ("view", _post_brief),
    "approve_po": ("settings", _approve_po),
    "save_mapping": ("settings", _save_mapping),
}


# ----------------------------------------------------------------------------- Claude with read-only tools
def llm_client(key):
    import anthropic
    return anthropic.Anthropic(api_key=key)


def run_ai(system, prompt, tools=None, key=None, client=None, max_steps=MAX_STEPS, max_tokens=2500):
    """Claude tool-use loop. tools: {name: (description, input_schema, function(**args) -> json-able)}.
    Returns (final text, trace of tool calls). Tool functions are read-only by design."""
    client = client or llm_client(key)
    tools = tools or {}
    spec = [dict(name=n, description=d, input_schema=s) for n, (d, s, _) in tools.items()]
    msgs = [{"role": "user", "content": prompt}]
    trace = []
    for _ in range(max_steps):
        kw = dict(model=MODEL, max_tokens=max_tokens, system=system, messages=msgs)
        if spec:
            kw["tools"] = spec
        resp = client.messages.create(**kw)
        content = list(resp.content)
        calls = [c for c in content if getattr(c, "type", "") == "tool_use"]
        if not calls:
            return "".join(getattr(c, "text", "") for c in content).strip(), trace
        msgs.append({"role": "assistant", "content": [c if isinstance(c, dict) else c.model_dump() if hasattr(c, "model_dump") else
                                                      {"type": c.type, "id": c.id, "name": c.name, "input": c.input} for c in content]})
        results = []
        for c in calls:
            fn = tools.get(c.name, (None, None, None))[2]
            try:
                out = fn(**(c.input or {})) if fn else {"error": f"unknown tool {c.name}"}
            except Exception as e:  # noqa: BLE001
                out = {"error": f"{type(e).__name__}: {e}"}
            trace.append(dict(tool=c.name, input=c.input))
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(out, default=str)[:12000]})
        msgs.append({"role": "user", "content": results})
    return "I stopped after the step limit without a final answer.", trace


def ask_json(system, prompt, key=None, client=None):
    """One call that must answer with a JSON object (used to turn free text into a structured request)."""
    text, _ = run_ai(system + "\nAnswer with one JSON object only, no prose.", prompt, key=key, client=client, max_steps=1, max_tokens=1500)
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def frame(df, n=40):
    """A small, model-friendly view of a table."""
    if df is None or len(df) == 0:
        return []
    return json.loads(df.head(n).to_json(orient="records", date_format="iso", default_handler=str))


def now():
    return pd.Timestamp.now()
