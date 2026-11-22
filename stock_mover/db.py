"""Persistent storage for StockFlow: PostgreSQL in production, a local SQLite file otherwise.

Set the connection in Streamlit secrets (or the DATABASE_URL environment variable):

    [database]
    url = "postgresql+psycopg://user:password@host:5432/stockflow?sslmode=require"

Without it, data goes to ./workspace/stockflow.db (SQLite). That file survives restarts on your own server but NOT on
Streamlit Community Cloud, which wipes local disk; the app shows a warning in that case.

Tables
  plans            one row per plan built (who, when, data, settings, input pack for exact rebuild after a restart)
  recommendations  every recommended line of every plan, with its line-level decision (approved / rejected / edited qty)
  released_orders  transfer-order lines released to the warehouse
  movements        dispatch and receipt events per transfer-order line
  runs, ratings    feedback history (kept for the existing Feedback tab)
  settings         feedback-accepted setting overrides
  whatsapp_log     alerts sent (last 4 digits only)
  users            app users (PBKDF2 hashes), roles, store scope, lockout counters
  audit            who did what, when (append-only from the app)
"""
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, Integer, LargeBinary, MetaData, String, Table, Text, create_engine,
                        delete, insert, inspect, select, update)

HERE = Path(__file__).resolve().parent
WORK = HERE / "workspace"
md = MetaData()

T = {}
T["plans"] = Table("plans", md, Column("plan_id", String(40), primary_key=True), Column("created_at", DateTime), Column("created_by", String(80)),
                   Column("plan_date", String(10)), Column("brand", String(120)), Column("data_label", String(300)), Column("source", String(20)),
                   Column("trips", Integer), Column("lines", Integer), Column("pieces", Integer), Column("net_benefit", Float),
                   Column("move_cost", Float), Column("rules_passed", Integer), Column("rules_total", Integer), Column("settings", JSON),
                   Column("input_pack", LargeBinary), Column("status", String(20), default="open"))
T["recommendations"] = Table(
    "recommendations", md, Column("plan_id", String(40), primary_key=True), Column("move_id", String(12), primary_key=True),
    Column("to_number", String(30)), Column("trip_key", String(120)), Column("type", String(40)), Column("grp", String(60)),
    Column("from_id", String(40)), Column("to_id", String(40)), Column("sku_id", String(80)), Column("style_name", String(160)),
    Column("size", String(20)), Column("qty", Integer), Column("value_mrp", Float), Column("net_benefit", Float), Column("priority", String(10)),
    Column("reason", Text), Column("decision", String(12), default="pending"), Column("approved_qty", Integer), Column("note", Text),
    Column("decided_by", String(80)), Column("decided_at", DateTime))
T["released_orders"] = Table(
    "released_orders", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("plan_id", String(40)), Column("to_number", String(30)),
    Column("line_no", Integer), Column("from_location", String(40)), Column("to_location", String(40)), Column("sku_id", String(80)),
    Column("qty", Integer), Column("ship_date", String(10)), Column("expected_receipt", String(10)), Column("move_type", String(40)),
    Column("reason_group", String(60)), Column("move_id", String(12)), Column("released_at", String(16)), Column("released_by", String(80)),
    Column("run_id", String(40)))
T["movements"] = Table("movements", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("to_number", String(30)),
                       Column("sku_id", String(80)), Column("event", String(12)), Column("qty", Integer), Column("at", String(25)),
                       Column("by", String(80)), Column("note", Text))
T["runs"] = Table("runs", md, Column("run_id", String(40), primary_key=True), Column("run_at", String(16)), Column("user", String(80)),
                  Column("plan_date", String(10)), Column("data", String(300)), Column("trips", Integer), Column("lines", Integer),
                  Column("pieces", Integer), Column("net_benefit", Float), Column("move_cost", Float), Column("rules_passed", Integer),
                  Column("rules_total", Integer))
T["ratings"] = Table("ratings", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("rated_at", String(16)),
                     Column("run_id", String(40)), Column("stars", Integer), Column("moves_right", Integer), Column("easy", Integer),
                     Column("saves_time", Integer), Column("changes", Text), Column("comment", Text), Column("name", String(80)))
T["settings"] = Table("settings", md, Column("key", String(40), primary_key=True), Column("value", JSON), Column("updated_at", DateTime),
                      Column("updated_by", String(80)))
T["whatsapp_log"] = Table("whatsapp_log", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("sent_at", String(16)),
                          Column("by", String(80)), Column("to", String(120)), Column("role", String(40)), Column("number", String(12)),
                          Column("provider", String(20)), Column("status", String(12)), Column("error", Text))
T["users"] = Table("users", md, Column("username", String(80), primary_key=True), Column("password", String(200)), Column("role", String(20)),
                   Column("stores", Text, default=""), Column("active", Boolean, default=True), Column("failed", Integer, default=0),
                   Column("locked_until", DateTime), Column("created_at", DateTime), Column("created_by", String(80)),
                   Column("last_login", DateTime), Column("must_change", Boolean, default=False))
T["audit"] = Table("audit", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("at", DateTime), Column("user", String(80)),
                   Column("role", String(20)), Column("action", String(60)), Column("object", String(200)), Column("details", JSON))

# operations layer
T["ingestions"] = Table("ingestions", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("at", DateTime),
                        Column("by", String(80)), Column("source", String(200)), Column("profile", String(80)), Column("sheet", String(40)),
                        Column("file", String(300)), Column("rows_in", Integer), Column("rows_out", Integer), Column("rejected", Integer),
                        Column("mapping", JSON), Column("issues", JSON), Column("status", String(12)))
T["exceptions"] = Table("exceptions", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("key", String(200)),
                        Column("kind", String(30)), Column("severity", String(10)), Column("region", String(80)), Column("location", String(40)),
                        Column("ref", String(120)), Column("title", Text), Column("detail", Text), Column("value", Float),
                        Column("first_seen", DateTime), Column("last_seen", DateTime), Column("seen_count", Integer, default=1),
                        Column("status", String(12), default="open"), Column("owner", String(80)), Column("resolved_by", String(80)),
                        Column("resolved_at", DateTime), Column("resolution", Text), Column("source", String(40)))
T["jobs"] = Table("jobs", md, Column("job_id", String(40), primary_key=True), Column("kind", String(30)), Column("status", String(12)),
                  Column("requested_by", String(80)), Column("requested_at", DateTime), Column("started_at", DateTime),
                  Column("finished_at", DateTime), Column("step", String(120)), Column("progress", Float), Column("plan_id", String(40)),
                  Column("error", Text), Column("log", Text), Column("host", String(80)))
T["trip_decisions"] = Table("trip_decisions", md, Column("plan_id", String(40), primary_key=True), Column("to_number", String(30), primary_key=True),
                            Column("region", String(80)), Column("decision", String(12)), Column("by", String(80)), Column("at", DateTime),
                            Column("note", Text))
T["erp_exchange"] = Table("erp_exchange", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("at", DateTime),
                          Column("direction", String(4)), Column("channel", String(20)), Column("target", String(300)), Column("kind", String(30)),
                          Column("file", String(300)), Column("records", Integer), Column("status", String(12)), Column("error", Text),
                          Column("by", String(80)), Column("checksum", String(64)))

T["catalog"] = Table("catalog", md, Column("sku_id", String(80), primary_key=True), Column("name", String(200)), Column("barcode", String(40)),
                     Column("category", String(120)), Column("updated_at", DateTime))
T["stock_counts"] = Table("stock_counts", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("count_id", String(40)),
                          Column("location_id", String(40)), Column("sku_id", String(80)), Column("qty", Integer), Column("counted_on", String(10)),
                          Column("counted_at", DateTime), Column("by", String(80)), Column("method", String(40)), Column("source", String(300)),
                          Column("confidence", Float), Column("scope", String(10)), Column("note", Text))

T["plan_trips"] = Table("plan_trips", md, Column("plan_id", String(40), primary_key=True), Column("to_number", String(30), primary_key=True),
                        Column("from_id", String(40)), Column("to_id", String(40)), Column("dispatch", String(10)), Column("arrives", String(10)),
                        Column("lines", Integer), Column("pieces", Integer), Column("value_mrp", Float), Column("net_benefit", Float),
                        Column("urgent", Integer), Column("needs_approval", Boolean), Column("main_group", String(60)), Column("reason", Text))
T["plan_orders"] = Table("plan_orders", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("plan_id", String(40)),
                         Column("to_number", String(30)), Column("line_no", Integer), Column("from_location", String(40)),
                         Column("to_location", String(40)), Column("sku_id", String(80)), Column("qty", Integer), Column("ship_date", String(10)),
                         Column("expected_receipt", String(10)), Column("move_type", String(40)), Column("reason_group", String(60)),
                         Column("move_id", String(12)))
T["api_keys"] = Table("api_keys", md, Column("key_id", String(20), primary_key=True), Column("hash", String(128)), Column("name", String(120)),
                      Column("role", String(20)), Column("stores", Text, default=""), Column("created_by", String(80)),
                      Column("created_at", DateTime), Column("last_used", DateTime), Column("active", Boolean, default=True))
T["webhook_log"] = Table("webhook_log", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("at", DateTime),
                         Column("event", String(60)), Column("url", String(300)), Column("status", String(12)), Column("code", Integer),
                         Column("error", Text), Column("attempts", Integer))

T["kpi_history"] = Table("kpi_history", md, Column("id", Integer, primary_key=True, autoincrement=True), Column("plan_id", String(40)),
                         Column("plan_date", String(10)), Column("store_id", String(40)), Column("category", String(120)),
                         Column("on_hand", Integer), Column("pred7", Float), Column("selling", Integer), Column("in_stock", Integer),
                         Column("at_risk", Integer), Column("old_units", Integer), Column("at", DateTime))

_ENGINE = None
_URL = None


def _secret_url():
    try:
        import streamlit as st
        if "database" in st.secrets and st.secrets["database"].get("url"):
            return st.secrets["database"]["url"]
        if st.secrets.get("DATABASE_URL"):
            return st.secrets["DATABASE_URL"]
    except Exception:  # noqa: BLE001  (no secrets file / not running in Streamlit)
        pass
    return os.environ.get("DATABASE_URL", "")


PLACEHOLDER = None          # set when the configured URL is still the example text (shown on the Admin tab and sign-in)


def _is_placeholder(u):
    """The example from secrets.example.toml (USER / PASSWORD / HOST / DBNAME) or an obviously unfinished URL."""
    try:
        from sqlalchemy.engine import make_url
        x = make_url(u.replace("postgres://", "postgresql://", 1))
        return (x.host or "").upper() in ("HOST", "HOSTNAME", "YOUR-HOST", "") or (x.database or "").upper() in ("DBNAME", "DATABASE") \
            or (x.username or "").upper() == "USER" and (x.password or "").upper() == "PASSWORD"
    except Exception:  # noqa: BLE001
        return True


def url():
    global PLACEHOLDER
    u = _secret_url()
    if u and not u.startswith("sqlite") and _is_placeholder(u):
        PLACEHOLDER = "The database URL in secrets is still the example text, so a temporary local database is used. " \
                      "Paste your real PostgreSQL connection string in Settings → Secrets."
        u = ""
    if u.startswith("postgres://"):
        u = "postgresql+psycopg://" + u[len("postgres://"):]
    elif u.startswith("postgresql://"):
        u = "postgresql+psycopg://" + u[len("postgresql://"):]
    if not u:
        WORK.mkdir(exist_ok=True)
        u = f"sqlite:///{WORK / 'stockflow.db'}"
    return u


def engine():
    global _ENGINE, _URL
    u = url()
    if _ENGINE is None or u != _URL:
        _ENGINE = create_engine(u, pool_pre_ping=True, future=True)
        _URL = u
        md.create_all(_ENGINE)
        _migrate_csv()
    return _ENGINE


def backend():
    u = url()
    return ("PostgreSQL", True) if u.startswith("postgresql") else ("SQLite (local file)", False)


def describe():
    """Safe description for the UI: never shows the password."""
    u = url()
    if u.startswith("sqlite"):
        p = Path(u.split("///", 1)[-1])
        return f"SQLite file {p.name} in {'./workspace' if p.parent == WORK else p.parent}"
    try:
        from sqlalchemy.engine import make_url
        x = make_url(u)
        return f"PostgreSQL · {x.host}:{x.port or 5432}/{x.database} as {x.username}"
    except Exception:  # noqa: BLE001
        return "PostgreSQL"


# ---------------------------------------------------------------- generic helpers
def _clean(v):
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return v
    return v


def insert_rows(table, rows):
    rows = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else (rows if isinstance(rows, list) else [rows])
    if not rows:
        return
    t = T[table]
    cols = set(t.c.keys())
    rows = [{k: _clean(v) for k, v in r.items() if k in cols} for r in rows]
    with engine().begin() as c:
        c.execute(insert(t), rows)


def read(table, where=None, order=None, limit=None):
    t = T[table]
    q = select(t)
    for k, v in (where or {}).items():
        q = q.where(t.c[k].in_(v) if isinstance(v, (list, tuple, set)) else t.c[k] == v)
    if order:
        q = q.order_by(*[(t.c[o[1:]].desc() if o.startswith("-") else t.c[o]) for o in order])
    if limit:
        q = q.limit(limit)
    with engine().connect() as c:
        return pd.DataFrame(c.execute(q).mappings().all(), columns=list(t.c.keys()))


def update_rows(table, where, values):
    t = T[table]
    q = update(t)
    for k, v in where.items():
        q = q.where(t.c[k] == v)
    with engine().begin() as c:
        c.execute(q.values(**{k: _clean(v) for k, v in values.items()}))


def delete_rows(table, where):
    t = T[table]
    q = delete(t)
    for k, v in where.items():
        q = q.where(t.c[k] == v)
    with engine().begin() as c:
        c.execute(q)


def upsert_setting(key, value, user=""):
    with engine().begin() as c:
        t = T["settings"]
        c.execute(delete(t).where(t.c.key == key))
        c.execute(insert(t).values(key=key, value=value, updated_at=datetime.now(), updated_by=user))


def get_setting(key, default=None):
    d = read("settings", {"key": key})
    return d.value.iloc[0] if len(d) else default


# ---------------------------------------------------------------- one-time import of the older CSV files
def _migrate_csv():
    flag = "migrated_csv_v1"
    with _ENGINE.connect() as c:
        if c.execute(select(T["settings"]).where(T["settings"].c.key == flag)).first():
            return
    m = {"runs.csv": "runs", "ratings.csv": "ratings", "released_transfer_orders.csv": "released_orders", "movements.csv": "movements",
         "whatsapp_log.csv": "whatsapp_log"}
    for f, t in m.items():
        p = WORK / f
        if p.exists():
            try:
                df = pd.read_csv(p, dtype=str)
                for col in ("qty", "line_no", "stars", "moves_right", "easy", "saves_time", "trips", "lines", "pieces", "rules_passed", "rules_total"):
                    if col in df:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
                insert_rows(t, df)
            except Exception:  # noqa: BLE001  (a damaged old file must not stop the app)
                pass
    if (WORK / "receipts.csv").exists():
        try:
            r = pd.read_csv(WORK / "receipts.csv", dtype=str).rename(columns={"qty_received": "qty", "recorded_at": "at", "recorded_by": "by"})
            r["event"], r["note"] = "received", "imported"
            r["qty"] = pd.to_numeric(r.qty, errors="coerce").fillna(0).astype(int)
            insert_rows("movements", r)
        except Exception:  # noqa: BLE001
            pass
    if (WORK / "settings.json").exists():
        try:
            upsert_setting("overrides", json.loads((WORK / "settings.json").read_text()), "migration")
        except Exception:  # noqa: BLE001
            pass
    with _ENGINE.begin() as c:
        c.execute(insert(T["settings"]).values(key=flag, value=True, updated_at=datetime.now(), updated_by="system"))


def tables():
    return sorted(inspect(engine()).get_table_names())
