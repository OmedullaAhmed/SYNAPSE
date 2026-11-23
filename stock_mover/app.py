"""StockFlow app: upload a brand's data pack, get the day's stock moves, their money, and the trips to approve.

Start:  streamlit run app.py
"""
import html
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import audit
import monitoring
import erp
import exceptions_queue as XQ
import ingest
import jobs
import pipeline
import auth
import chat
import db
import engine as E
import feedback as fb
import insights as ins
import notify
import plans
import safety
import schema

HERE = Path(__file__).parent
BRANDS = sorted(b for b in (HERE / "brands").glob("*.json") if not b.stem.startswith("demo"))
DEMO = HERE / "data" / "demo"
DEMO2 = HERE / "data" / "demo_general"
PROFILE_NAMES = {"apparel": "Apparel, footwear & ethnic wear", "general_retail": "General retail (mixed goods)",
                 "electronics": "Electronics & appliances", "grocery_fmcg": "Grocery / FMCG (expiry-aware)",
                 "pharmacy": "Pharmacy & health (expiry-critical)", "spare_parts": "Spare parts & hardware",
                 "sample_ethnic_chain": "Sample ethnic-wear chain (hackathon data)"}
IN_BROWSER = sys.platform == "emscripten"   # running as a static web page (stlite): no server, no file downloads
st.set_page_config(page_title="StockFlow · stock planning for stores and warehouses", page_icon="📦", layout="wide",
                   initial_sidebar_state="collapsed")

# ----------------------------------------------------------------------------- look and feel
INK, MUTED, LINE, BG = "#17212B", "#5E6875", "#E7E4DE", "#F6F5F1"
TEAL, TEAL_SOFT, SAFFRON, PLUM, RED, GREEN = "#1E5C61", "#E4EFEE", "#D9962B", "#8A3B5C", "#C2413A", "#2E7D4F"
GCOL = dict(zip(E.GROUPS, ["#C2413A", "#D9962B", "#B5652A", "#7D8792", "#1E5C61", "#8A3B5C", "#A8648A", "#3E4C59", "#2E8A7B"]))
GICON = dict(zip(E.GROUPS, ["🚨", "🎉", "🧩", "🪟", "📦", "♻️", "🔗", "↩️", "🏷️"]))


def css(color=TEAL):
    st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');
:root{{--p:{color};--ink:{INK};--mut:{MUTED};--line:{LINE};--bg:{BG};--soft:{TEAL_SOFT};--acc:{SAFFRON};--red:{RED};--green:{GREEN}}}
html,body,[class*="css"],.stApp{{font-family:Inter,system-ui,sans-serif;color:var(--ink)}}
.stApp{{background:var(--bg)}}
h1,h2,h3,h4,.sf-h{{font-family:'Plus Jakarta Sans',Inter,sans-serif!important;letter-spacing:-.01em;color:var(--ink)}}
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{{visibility:hidden;height:0}}
header[data-testid="stHeader"]{{background:transparent;height:0}}
.block-container{{padding:1.2rem 1.4rem 4rem;max-width:1160px}}
/* top bar */
.sf-top{{display:flex;align-items:center;gap:.6rem;margin:.2rem 0 1rem}}
.sf-logo{{width:38px;height:38px;border-radius:10px;background:var(--p);display:grid;place-items:center;color:#fff;font-size:20px;flex:none}}
.sf-name{{font:800 1.25rem/1 'Plus Jakarta Sans',sans-serif}} .sf-name span{{color:var(--p)}}
.sf-sub{{font-size:.78rem;color:var(--mut)}}
.sf-chip{{margin-left:auto;background:#fff;border:1px solid var(--line);border-radius:999px;padding:.3rem .8rem;font-size:.8rem;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:45%}}
/* hero */
.hero{{background:linear-gradient(135deg,var(--p) 0%,color-mix(in srgb,var(--p) 55%,#000) 100%);color:#fff;border-radius:18px;padding:clamp(1.2rem,3.5vw,2.4rem);margin-bottom:1.1rem;position:relative;overflow:hidden}}
.hero:after{{content:"";position:absolute;right:-60px;top:-60px;width:240px;height:240px;border-radius:50%;background:rgba(255,255,255,.07)}}
.hero h1{{color:#fff!important;font-size:clamp(1.5rem,4.2vw,2.4rem)!important;line-height:1.12!important;margin:0 0 .5rem!important;padding:0!important;max-width:760px}}
.hero p{{color:rgba(255,255,255,.86);font-size:clamp(.95rem,2.2vw,1.06rem);max-width:680px;margin:0}}
.hero .meta{{display:flex;flex-wrap:wrap;gap:.45rem;margin-top:1rem}}
.hero.sm{{padding:1rem 1.3rem}} .hero.sm h1{{font-size:clamp(1.3rem,3.4vw,1.9rem)!important}}
.hero .meta span{{background:rgba(255,255,255,.14);border-radius:999px;padding:.25rem .7rem;font-size:.8rem;white-space:nowrap}}
/* cards */
.grid{{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));margin:.2rem 0 1rem}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:1rem 1.1rem}}
.card h4{{margin:.2rem 0 .3rem!important;font-size:1.02rem!important;padding:0!important}}
.card p{{margin:0;color:var(--mut);font-size:.9rem;line-height:1.5}}
.card .ic{{font-size:1.4rem}}
.kpi{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:.9rem 1rem;position:relative;overflow:hidden}}
.kpi:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c,var(--p))}}
.kpi .l{{font-size:.78rem;color:var(--mut);font-weight:600}}
.kpi .v{{font:800 clamp(1.3rem,3.6vw,1.75rem)/1.15 'Plus Jakarta Sans',sans-serif;margin:.25rem 0;font-variant-numeric:tabular-nums;white-space:nowrap}}
.kpi .s{{font-size:.78rem;color:var(--mut);line-height:1.35}}
.ins{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:.85rem 1rem;margin:.55rem 0;display:flex;gap:.7rem;line-height:1.5;font-size:.95rem}}
.ins .dot{{flex:none;width:10px;height:10px;border-radius:50%;background:var(--c);margin-top:.45rem}}
.note{{border-radius:12px;padding:.65rem .9rem;font-size:.88rem;margin:0 0 .9rem;line-height:1.45}}
.note.warn{{background:#FFF5E6;border:1px solid #F1D2A0}} .note.ok{{background:var(--soft);border:1px solid #C9DEDC}}
.step{{display:flex;gap:.8rem;align-items:flex-start}} .step b.n{{flex:none;width:30px;height:30px;border-radius:50%;background:var(--soft);color:var(--p);display:grid;place-items:center;font-weight:800}}
.sec{{font:800 1.15rem 'Plus Jakarta Sans',sans-serif;margin:1.2rem 0 .5rem}}
.chip{{display:inline-block;border-radius:999px;padding:.12rem .55rem;font-size:.74rem;font-weight:600;white-space:nowrap;border:1px solid currentColor}}
.trip{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:.85rem 1rem;margin:.5rem 0}}
.trip .r{{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .6rem}}
.trip .route{{font-weight:700;font-size:1rem}} .trip .to{{color:var(--mut);font-size:.8rem;font-weight:600}}
.trip .nums{{margin-left:auto;text-align:right;font-variant-numeric:tabular-nums;font-size:.86rem;white-space:nowrap}}
.trip .why{{color:var(--mut);font-size:.88rem;margin-top:.35rem;line-height:1.45}}
.bar{{height:8px;border-radius:4px;background:#EEEBE5;overflow:hidden;margin-top:.5rem}} .bar i{{display:block;height:100%;background:var(--c,var(--p))}}
/* streamlit widgets */
.stTabs [data-baseweb="tab-list"]{{gap:.25rem;overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none;border-bottom:1px solid var(--line)}}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar{{display:none}}
.stTabs [data-baseweb="tab"]{{height:44px;padding:0 .9rem;border-radius:10px 10px 0 0;white-space:nowrap;font-weight:600}}
.stTabs [aria-selected="true"]{{color:var(--p)!important}}
.stButton>button,.stDownloadButton>button{{border-radius:12px;min-height:46px;font-weight:600}}
.stButton>button[kind="primary"],.stDownloadButton>button[kind="primary"]{{background:var(--p);border-color:var(--p);color:#fff}}
.stButton>button:disabled{{opacity:.45;color:#fff}}
[data-testid="stExpander"]{{background:#fff;border-radius:14px!important;border:1px solid var(--line)!important}}
[data-testid="stExpander"] summary{{font-weight:600}}
[data-testid="stVerticalBlockBorderWrapper"]>div[data-testid="stVerticalBlock"]{{gap:.5rem}}
div[data-testid="stContainer"]{{border-radius:14px}}
[data-testid="stFileUploaderDropzone"]{{border-radius:14px;background:#fff}}
@media (max-width:640px){{
 .block-container{{padding:.7rem .75rem 4rem}}
 .grid.k{{grid-template-columns:1fr 1fr;gap:.55rem}}
 .kpi{{padding:.7rem .75rem}} .kpi .s{{font-size:.72rem}}
 .hero{{border-radius:14px}} .sf-chip{{display:none}} .hero.sm p{{display:none}}
 .trip .nums{{margin-left:0;text-align:left;width:100%}}
 .stTabs [data-baseweb="tab"]{{padding:0 .6rem;font-size:.9rem}}
}}
</style>""", unsafe_allow_html=True)


def bold(t):
    return re.sub(r"[*][*](.+?)[*][*]", r"<b>\1</b>", html.escape(str(t), quote=False))


def kpis(items):
    """items: (label, value, sub[, colour])"""
    out = []
    for it in items:
        l, v, s = it[:3]
        c = it[3] if len(it) > 3 else TEAL
        out.append(f'<div class="kpi" style="--c:{c}"><div class="l">{html.escape(l)}</div><div class="v">{html.escape(str(v))}</div>'
                   f'<div class="s">{html.escape(str(s))}</div></div>')
    st.markdown('<div class="grid k">' + "".join(out) + "</div>", unsafe_allow_html=True)


def note(text, kind="warn"):
    st.markdown(f'<div class="note {kind}">{text}</div>', unsafe_allow_html=True)


def sec(t):
    st.markdown(f'<div class="sec">{t}</div>', unsafe_allow_html=True)


def plot(fig, h=320, legend=True):
    fig.update_layout(height=h, margin=dict(l=4, r=8, t=40, b=4), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Inter, sans-serif", color=INK, size=13), title_font=dict(size=15),
                      legend=dict(orientation="h", yanchor="top", y=-0.15, x=0, title="") if legend else None, showlegend=legend)
    fig.update_xaxes(gridcolor="#ECE9E3", zeroline=False)
    fig.update_yaxes(gridcolor="#ECE9E3", zeroline=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "responsive": True})


def topbar(cfg, extra=""):
    st.markdown(f'<div class="sf-top"><div class="sf-logo">📦</div><div><div class="sf-name">Stock<span>Flow</span></div>'
                f'<div class="sf-sub">Stock planning for stores and warehouses</div></div>'
                f'<div class="sf-chip">{html.escape(extra or cfg["brand"]["name"])}</div></div>', unsafe_allow_html=True)


# ----------------------------------------------------------------------------- sign in (mandatory)
SECRETS = st.secrets if hasattr(st, "secrets") else None
monitoring.init("web")


def secret(*path, default=None):
    """A value from Streamlit secrets, or default when it (or the whole secrets file) is missing. Never raises."""
    try:
        cur = SECRETS
        for k in path:
            cur = cur[k]
        return cur
    except Exception:  # noqa: BLE001
        return default
USERS = auth.users(SECRETS)            # secrets users (kept from earlier versions)


def _set_session(name, rec):
    st.session_state.user, st.session_state.role = name, rec.get("role", "viewer")
    st.session_state.scope = [s_.strip() for s_ in str(rec.get("stores", "")).split(",") if s_.strip()]
    st.session_state.must_change = bool(rec.get("must_change")) and rec.get("source") == "database"
    st.session_state.last_active = datetime.now()


def sign_out(reason="logout"):
    audit.log(reason, st.session_state.get("user", ""), {}, st.session_state.get("user", ""), st.session_state.get("role", ""))
    sso = st.session_state.get("sso")
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    if sso:
        try:
            st.logout()
        except Exception:  # noqa: BLE001
            pass


def sso_providers():
    """Company sign-in (OIDC: Microsoft Entra ID, Google Workspace, Okta…) from Streamlit's [auth] secrets. [] when not set up."""
    try:
        a = SECRETS["auth"]
    except Exception:  # noqa: BLE001
        return []
    try:
        named = [k for k, v in dict(a).items() if hasattr(v, "keys") and "client_id" in v]
        return named or (["default"] if "client_id" in a else [])
    except Exception:  # noqa: BLE001
        return []


def sso_user():
    try:
        return st.user.get("email") if st.user.is_logged_in else None
    except Exception:  # noqa: BLE001
        return None


def guest_allowed():
    """Demo without sign-in is on unless secrets say  guest_demo = false  (top level or under [auth])."""
    for path_ in (("guest_demo",),):
        try:
            cur = SECRETS
            for k_ in path_:
                cur = cur[k_]
            return str(cur).strip().lower() not in ("false", "0", "no", "off")
        except Exception:  # noqa: BLE001
            continue
    return os.environ.get("STOCKFLOW_GUEST_DEMO", "true").strip().lower() not in ("false", "0", "no", "off")


def guest_button(key):
    if not guest_allowed():
        return
    st.markdown('<div style="text-align:center;color:#5E6875;margin:.6rem 0 .3rem">or</div>', unsafe_allow_html=True)
    if st.button("👀  Explore the demo without signing in", key=f"guest_{key}", width="stretch"):
        _set_session("guest", {"role": "guest"})
        st.session_state.guest = True
        st.session_state.auto_demo = True
        audit.log("login.guest", "guest", {}, "guest", "guest")
        st.rerun()


def login_gate():
    """No page is shown without a signed-in user (except the single-device browser build)."""
    if IN_BROWSER:
        return
    try:
        everyone = auth.all_users(SECRETS)
    except Exception as e:  # noqa: BLE001
        css()
        topbar(schema.DEFAULTS, "Unavailable")
        st.error(f"The database is not reachable ({type(e).__name__}). Check the database URL in the app's secrets.")
        st.stop()
    if "user" in st.session_state:
        last = st.session_state.get("last_active", datetime.now())
        if datetime.now() - last > timedelta(minutes=auth.IDLE_MINUTES):
            sign_out("session.timeout")
            st.session_state.timed_out = True
            st.rerun()
        st.session_state.last_active = datetime.now()
        if st.session_state.get("must_change"):
            css()
            topbar(schema.DEFAULTS, st.session_state.user)
            st.markdown('<div class="hero"><h1>Choose your own password</h1><p>Your account was created with a temporary password.</p></div>',
                        unsafe_allow_html=True)
            _, mid, _ = st.columns([1, 2, 1])
            with mid, st.form("chpw"):
                p1 = st.text_input("New password", type="password")
                p2 = st.text_input("Repeat it", type="password")
                if st.form_submit_button("Save password", type="primary", width="stretch"):
                    probs = auth.password_problems(p1, st.session_state.user)
                    if p1 != p2:
                        st.error("The two passwords differ.")
                    elif probs:
                        st.error("Password needs " + ", ".join(probs) + ".")
                    else:
                        auth.update_user(st.session_state.user, password=p1, must_change=False)
                        audit.log("user.password", st.session_state.user, {"self": True}, st.session_state.user, st.session_state.role)
                        st.session_state.must_change = False
                        st.rerun()
            st.stop()
        return
    css()
    if not everyone:
        topbar(schema.DEFAULTS, "First-time setup")
        st.markdown('<div class="hero"><h1>Create the first admin account</h1><p>Sign-in is required. This account can add other users '
                    'from the Admin tab.</p></div>', unsafe_allow_html=True)
        _, mid, _ = st.columns([1, 2, 1])
        with mid, st.form("setup"):
            u = st.text_input("Admin user name")
            p1 = st.text_input("Password (10+ characters, letters and numbers)", type="password")
            p2 = st.text_input("Repeat password", type="password")
            if st.form_submit_button("Create admin and sign in", type="primary", width="stretch"):
                if p1 != p2:
                    st.error("The two passwords differ.")
                else:
                    try:
                        auth.create_user(u, p1, "admin", "", "setup", must_change=False)
                        audit.log("user.create", u.strip(), {"role": "admin", "first_admin": True}, u.strip(), "admin")
                        _set_session(u.strip(), {"role": "admin", "source": "database"})
                        audit.log("login.ok", u.strip(), {}, u.strip(), "admin")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
        with mid:
            guest_button("setup")
        st.stop()
    provs = sso_providers()
    email = sso_user() if provs else None
    if email:                                        # signed in with the company account: map the email to a StockFlow user
        rec = next((v for k, v in everyone.items() if k.lower() == email.lower()), None)
        if rec and rec.get("active", True):
            _set_session(email.lower(), dict(rec, must_change=False))
            st.session_state.sso = True
            audit.log("login.sso", email.lower(), {"role": rec.get("role")}, email.lower(), rec.get("role", ""))
            try:
                auth.update_user(email.lower(), last_login=datetime.now(), failed=0) if rec.get("source") == "database" else None
            except Exception:  # noqa: BLE001
                pass
            st.rerun()
        topbar(schema.DEFAULTS, "No access")
        st.error(f"{email} signed in, but has no StockFlow access. Ask an admin to add this email as a user in the Admin tab.")
        audit.log("login.failed", email.lower(), {"sso": True, "reason": "not a user"}, email.lower(), "")
        if st.button("Sign out of the company account"):
            st.logout()
        st.stop()
    topbar(schema.DEFAULTS, "Sign in")
    st.markdown('<div class="hero"><h1>Welcome back</h1><p>Sign in to see today\'s stock plan.</p></div>', unsafe_allow_html=True)
    if provs:
        _, mid0, _ = st.columns([1, 2, 1])
        with mid0:
            for p_ in provs:
                if st.button("🔐  Sign in with your company account" + ("" if p_ == "default" else f" ({p_.title()})"),
                             type="primary", width="stretch", key=f"sso_{p_}"):
                    st.login() if p_ == "default" else st.login(p_)
            st.markdown('<div style="text-align:center;color:#5E6875;margin:.4rem 0">or with a StockFlow password</div>',
                        unsafe_allow_html=True)
    if st.session_state.pop("timed_out", False):
        note(f"You were signed out after {auth.IDLE_MINUTES} minutes without activity.", "ok")
    _, mid, _ = st.columns([1, 2, 1])
    with mid, st.form("login"):
        u = st.text_input("User name")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary", width="stretch"):
            ok, rec, msg = auth.attempt(u, p, SECRETS)
            if ok:
                _set_session(u.strip(), rec)
                audit.log("login.ok", u.strip(), {"source": rec.get("source")}, u.strip(), rec.get("role", ""))
                st.rerun()
            audit.log("login.locked" if "locked" in msg else "login.failed", u.strip(), {}, u.strip(), "")
            st.error(msg)
    with mid:
        guest_button("login")
    st.stop()


login_gate()
if os.environ.get("STOCKFLOW_SCHEDULER", "").lower() in ("on", "1", "true") and not IN_BROWSER:
    import daily
    daily.start_scheduler(dict(SECRETS) if SECRETS is not None else {})
GUEST = bool(st.session_state.get("guest"))
USER = st.session_state.get("user", "demo")
ROLE = st.session_state.get("role", "admin" if IN_BROWSER else "viewer")
OPEN = False                               # sign-in is now mandatory; kept for code that still reads it


def can_do(perm):
    return IN_BROWSER or auth.allowed(ROLE, perm)


def log(action, obj="", **details):
    audit.log(action, obj, details, USER, ROLE)


def account_bar():
    if IN_BROWSER:
        return
    if GUEST:
        a, b = st.columns([5, 1])
        a.caption("👀 Exploring the **demo brand** without signing in. You can try line approvals; uploading your own data, "
                  "releasing orders, tracking and settings need an account.")
        if b.button("Sign in", key="signout", width="stretch", type="primary"):
            sign_out()
            st.rerun()
        return
    name, is_pg = db.backend()
    if db.PLACEHOLDER and can_do("settings"):
        note("⚠️ " + html.escape(db.PLACEHOLDER))
    a, b = st.columns([5, 1])
    a.caption(f"Signed in as **{USER}** ({ROLE}) · data stored in {name}"
              + ("" if is_pg else " — set a PostgreSQL URL in secrets so history survives restarts"))
    if b.button("Sign out", key="signout", width="stretch"):
        sign_out()
        st.rerun()


# ----------------------------------------------------------------------------- data in
def unpack(uploads, dest):
    cfg_over = None
    for f in uploads:
        name, data = Path(f.name).name, f.getvalue()
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.infolist():
                    p = Path(m.filename)
                    if m.is_dir() or p.is_absolute() or ".." in p.parts:
                        continue
                    if p.suffix.lower() in (".csv", ".xlsx", ".xlsm"):
                        out = dest / "zip" / p
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(z.read(m))
                    elif p.name.lower() == "config.json":
                        cfg_over = json.loads(z.read(m))
        elif name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            (dest / name).write_bytes(data)
        elif name.lower().endswith(".json"):
            cfg_over = json.loads(data)
    return cfg_over


def template_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, txt in schema.template_csvs().items():
            z.writestr(f"stockflow_template/{n}", txt)
        z.writestr("stockflow_template/config.json", json.dumps({k: schema.DEFAULTS[k] for k in ("brand", "planning", "economics", "season")}, indent=2))
        z.writestr("stockflow_template/DATA_GUIDE.csv", pd.DataFrame(schema.guide_rows()).to_csv(index=False))
    return buf.getvalue()


NEED = {"required": ("Required", RED), "recommended": ("Recommended", SAFFRON), "optional": ("Optional", MUTED)}


def data_guide(key):
    st.markdown("You need **4 sheets** to start: **stores, items, sales, stock_snapshot**. Add **stock_weekly** to catch hidden stockouts, "
                "**lanes** for real freight costs and **cost_price** for real margins. Upload CSVs, one Excel workbook (a tab per sheet) "
                "or a zip. Customer and staff columns are dropped on load.")
    cards = []
    for t, (need, purpose, cols, _) in schema.TABLES.items():
        lab, col = NEED[need]
        req = ", ".join(c for c, cn, *_ in cols if cn == "required")
        cards.append(f'<div class="card"><span class="chip" style="color:{col}">{lab}</span><h4>{t}.csv</h4><p>{html.escape(purpose)}</p>'
                     f'<p style="margin-top:.4rem;font-size:.8rem"><b>Must have:</b> {html.escape(req)}</p></div>')
    st.markdown('<div class="grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    with st.expander("Every column, with examples"):
        rows = pd.DataFrame(schema.guide_rows())
        st.dataframe(rows, hide_index=True, width="stretch", height=420)
    st.download_button("⬇️ Blank templates + data guide (zip)", template_zip(), "stockflow_templates.zip", key=f"tpl_{key}", width="stretch")


def run_engine(source, cfg):
    return E.run(source, Path(tempfile.mkdtemp(prefix="stockflow_out_")), cfg)


def settings_panel(base):
    dis = not can_do("settings")
    a, b, c = st.columns(3)
    base["brand"]["name"] = a.text_input("Brand name", base["brand"]["name"], disabled=dis)
    base["brand"]["number_style"] = b.selectbox("Numbers", ["indian", "international"],
                                                index=0 if base["brand"]["number_style"] == "indian" else 1, disabled=dis,
                                                format_func=lambda x: "Lakh / Crore" if x == "indian" else "Thousand / Million")
    base["brand"]["currency"] = c.text_input("Currency symbol", base["brand"]["currency"], disabled=dis)
    a, b = st.columns(2)
    for i, ((sec_, k), (lo, hi, step, name)) in enumerate(schema.TUNABLE.items()):
        base[sec_][k] = (a if i % 2 == 0 else b).slider(name, lo, hi, int(base[sec_][k]), step=step, key=f"s_{sec_}_{k}", disabled=dis)
    a, b = st.columns(2)
    base["planning"]["approval_value"] = a.number_input("One line above this needs sign-off (MRP)", value=int(base["planning"]["approval_value"]),
                                                        step=10000, disabled=dis)
    base["planning"]["trip_approval_value"] = b.number_input("One trip above this needs sign-off (MRP)",
                                                             value=int(base["planning"]["trip_approval_value"]), step=50000, disabled=dis)
    a, b, c = st.columns(3)
    base["economics"]["handling_per_unit"] = a.number_input("Handling cost per piece", value=float(base["economics"]["handling_per_unit"]), disabled=dis)
    base["economics"]["default_cost_ratio"] = b.number_input("Cost as share of MRP (no cost_price)", value=float(base["economics"]["default_cost_ratio"]),
                                                             min_value=0.05, max_value=0.95, step=0.05, disabled=dis)
    base["economics"]["markdown_per_month"] = c.number_input("Value old stock loses a month", value=float(base["economics"]["markdown_per_month"]),
                                                             min_value=0.0, max_value=0.3, step=0.01, disabled=dis)
    st.caption("Freight per route comes from lanes.csv; other routes use the defaults. Business rules never relax."
               + (" Only an admin can change these." if dis else ""))


def start(source, cfg, label, reopen=None, cleanup=None):
    """Build a plan. source None = the uploaded files; reopen = plan_id of a saved plan being rebuilt (not saved again).
    The work runs as a background job (pipeline.build_plan): the page stays usable and shows progress; the plan is saved
    by the job itself, so it is kept even if the browser is closed."""
    tmp = cleanup                                          # a folder this call (then the job) owns and deletes
    pack, kind = None, "demo" if source in (DEMO, DEMO2) else "upload"
    try:
        if source is None:
            tmp = Path(tempfile.mkdtemp(prefix="stockflow_in_"))
            over = unpack(st.session_state.ups, tmp)
            cfg = schema.merge(cfg, over) if over else cfg
            pack = plans.pack_uploads(st.session_state.ups)
            source = tmp
        std_on = bool(st.session_state.get("standardise", True))
        user, role, own = USER, ROLE, tmp                  # `own` is fixed now; `tmp` is cleared below once the job owns it

        def work(progress):
            try:
                return pipeline.build_plan(source, cfg, label, user, kind, pack, progress, standardise=std_on, reopen=reopen, role=role)
            finally:
                if own:
                    shutil.rmtree(own, ignore_errors=True)
        if IN_BROWSER:                                     # no threads in the browser-only copy
            with st.spinner("Checking your data, measuring selling speed, planning and pricing every move…"):
                finish_plan(work(lambda *_: None), label, reopen)
            return
        tmp = None                                         # the job owns the folder now
        job = jobs.submit("plan" if not reopen else "reopen", work, USER, plan_id=reopen, label=f"Starting: {label}"[:120])
        st.session_state.job = dict(id=job, label=label, reopen=reopen)
        st.rerun()
    except E.DataError as e:
        st.error("**The data isn't quite what StockFlow needs.** Please fix:\n\n" + "\n".join(f"- {p}" for p in str(e).splitlines()))
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("RerunException", "StopException"):
            raise
        st.error(f"Something went wrong while planning: {type(e).__name__}: {e}")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def finish_plan(res, label, reopen=None):
    R = res["R"]
    st.session_state.R, st.session_state.label = R, label
    st.session_state.run_id = res["plan_id"]
    if res.get("save_error"):
        st.session_state.save_error = res["save_error"]
    if reopen:
        log("plan.reopen", reopen, trips=len(R["trips"]))
    st.session_state.pop("released", None)
    st.session_state.chat = []
    st.session_state.pop("ctx", None)
    st.rerun()


def job_panel():
    """Progress of the plan being built in the background; picks up the result when it is ready."""
    j = st.session_state.get("job")
    if not j:
        return

    @st.fragment(run_every=1.2)
    def _watch():
        s_ = jobs.status(j["id"]) or {}
        state = s_.get("status", "queued")
        if state in ("queued", "running"):
            with st.container(border=True):
                st.markdown(f"**⏳ Building the plan:** {html.escape(j['label'])}")
                st.progress(float(s_.get("progress") or 0.0), text=str(s_.get("step") or "Waiting for a free worker"))
                st.caption("This runs in the background: you can keep using the app, and the plan is saved even if you close the page.")
            return
        res = jobs.result(j["id"])
        if not isinstance(res, dict) and j.get("reopen") and state == "done":
            cached = pipeline.cache_get(j["reopen"])      # another session started the same reopen
            res = dict(R=cached, plan_id=j["reopen"]) if cached is not None else None
        st.session_state.pop("job", None)
        if state == "done" and isinstance(res, dict):
            finish_plan(res, j["label"], j.get("reopen"))
        elif state == "done":
            st.session_state.job_note = "The plan was built and saved. Open it from 'Pick up where you left off'."
        else:
            err = res if isinstance(res, Exception) else None
            if isinstance(err, E.DataError):
                st.session_state.job_note = "**The data isn't quite what StockFlow needs.** Please fix:\n\n" + "\n".join(
                    f"- {p}" for p in str(err).splitlines())
            else:
                st.session_state.job_note = f"The plan could not be built: {s_.get('error') or state}."
        st.rerun(scope="app")
    _watch()


def ai_vision_on():
    """AI reading of handwriting/photos: an admin switched it on (data-processing confirmed) and the server has a key."""
    try:
        on = bool((db.get_setting("ai_vision", {}) or {}).get("enabled"))
    except Exception:  # noqa: BLE001
        on = False
    key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        key = key or secret("ANTHROPIC_API_KEY")
    except Exception:  # noqa: BLE001
        pass
    return on and bool(key), key


def stock_from_images(key):
    """Photos or scans of count sheets, registers, delivery notes or barcodes -> a reviewed stock count."""
    import stockcount
    import vision
    if GUEST:
        st.caption("Sign in to count stock from photos.")
        return
    cat = stockcount.catalog()
    if cat.empty and "R" in st.session_state:
        it_ = st.session_state.R["P"]["items"].reset_index()
        cat = pd.DataFrame({"sku_id": it_.sku_id, "name": it_.style_name, "barcode": it_.get("barcode", "")})
    ai_ok, api_key = ai_vision_on()
    locs = sorted(XQ.locations()) or []
    a, b, c = st.columns([2, 1.3, 1.3])
    loc = a.selectbox("Location counted", locs, key=f"im_loc_{key}") if locs else a.text_input("Location code counted", key=f"im_loc_{key}")
    day = b.date_input("Stock as of", value=datetime.now().date() - timedelta(days=1), key=f"im_day_{key}",
                       help="The closing-stock day this count stands for. It is used by the plan for that day.")
    scope = c.selectbox("This count is", ["partial", "full"], key=f"im_scope_{key}",
                        format_func=lambda x: "Some items" if x == "partial" else "Everything at the location",
                        help="Everything: items not in the count are set to 0 at that location.")
    opts = ["Barcodes (each scan = 1 unit)", "Printed sheets and registers"] + (["Handwriting and photos (AI)"] if ai_ok else [])
    meth = st.multiselect("Read", opts, default=opts[:2] if not ai_ok else [opts[0], opts[2]], key=f"im_m_{key}")
    files = st.file_uploader("Photos or scans (JPG, PNG, PDF; several at once)", type=["jpg", "jpeg", "png", "webp", "pdf", "tif", "tiff"],
                             accept_multiple_files=True, key=f"im_up_{key}")
    cam = st.camera_input("…or take a photo", key=f"im_cam_{key}") if st.toggle("Use the camera", False, key=f"im_camt_{key}") else None
    if not ai_ok:
        st.caption("Handwritten registers need AI reading, which an admin can switch on in Integrations.")
    src = [(f.name, f.getvalue()) for f in (files or [])] + ([("camera.jpg", cam.getvalue())] if cam else [])
    if st.button("🔍 Read the images", type="primary", disabled=not src or not meth or not loc, key=f"im_go_{key}"):
        methods = tuple(m for m, w in (("barcode", "Barcodes"), ("ocr", "Printed"), ("ai", "Handwriting")) if any(x.startswith(w) for x in meth))
        with st.spinner("Reading…"):
            df, notes_ = vision.extract(src, cat, methods, api_key if "ai" in methods else None)
        st.session_state[f"im_res_{key}"] = (vision.summarise(df), notes_, ", ".join(n for n, _ in src)[:300], methods)
        log("vision.read", str(loc), files=len(src), methods=",".join(methods), lines=len(df), ai="ai" in methods)
    res = st.session_state.get(f"im_res_{key}")
    if res:
        sm, notes_, srcname, methods = res
        st.caption(" · ".join(notes_))
        if sm.empty:
            st.warning("Nothing readable was found. Try a sharper, flatter photo in good light, or the AI reading for handwriting.")
            return
        low0 = (sm.confidence < 0.7) | (sm.sku_id == "")
        sm = pd.concat([sm[low0], sm[~low0]], ignore_index=True)      # lines to check first
        low = (sm.confidence < 0.7) | (sm.sku_id == "")
        kpis([("Items read", f"{int((sm.sku_id != '').sum())}", f"{int(sm[sm.sku_id != ''].qty.sum()):,} units"),
              ("Check these", f"{int(low.sum())}", "unclear or not in the product list", SAFFRON if low.any() else GREEN)])
        ed = st.data_editor(sm.assign(check=low.values), hide_index=True, width="stretch", num_rows="dynamic", key=f"im_ed_{key}",
                            column_order=["check", "sku_id", "name", "qty", "confidence", "matched_by", "method", "lines"],
                            disabled=["check", "name", "confidence", "matched_by", "method", "lines"],
                            column_config={"check": st.column_config.CheckboxColumn("⚠️", help="Check this line"),
                                           "sku_id": st.column_config.TextColumn("SKU", help="Type the right code for any line not found"),
                                           "qty": st.column_config.NumberColumn("qty", min_value=0, step=1),
                                           "confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})
        unknown = set(ed.sku_id.astype(str)) - set(cat.sku_id) - {"", "None", "nan"}
        if unknown and len(cat):
            st.warning(f"{len(unknown)} codes are not in the product list and will be saved as typed: {', '.join(sorted(unknown)[:6])}")
        a, b = st.columns(2)
        if a.button(f"💾 Save count for {loc}", type="primary", width="stretch", key=f"im_save_{key}"):
            ed_ = ed[ed.sku_id.astype(str).str.strip().isin(["", "None", "nan"]) == False]  # noqa: E712
            cid, n = stockcount.save(loc, day.isoformat(), ed_.assign(qty=ed_.qty.fillna(0)), USER,
                                     "photo:" + "+".join(methods), srcname, scope)
            st.session_state.pop(f"im_res_{key}", None)
            st.success(f"Saved {n} lines for {loc} ({day:%d %b}). The next plan for that day uses them; differences from the system "
                       "show on the Exceptions tab.")
        snap_ = pd.DataFrame({"as_of_date": day.isoformat(), "location_id": loc, "sku_id": ed.sku_id, "on_hand": ed.qty})
        b.download_button("⬇️ As stock_snapshot.csv", safety.csv(snap_[snap_.sku_id.astype(str) != ""]), f"stock_{loc}_{day}.csv",
                          width="stretch", key=f"im_dl_{key}")


def data_panel(key="main"):
    if GUEST:
        with st.container(border=True):
            st.markdown('<div class="sf-h" style="font-weight:800;font-size:1.05rem">Plan with your own data</div>', unsafe_allow_html=True)
            st.caption("Sign in to upload a brand's own sheets. The guest demo only uses the fictional demo brand.")
            if st.button("Sign in", key=f"guest_signin_{key}", width="stretch"):
                sign_out()
                st.rerun()
        return
    with st.container(border=True):
        st.markdown('<div class="sf-h" style="font-weight:800;font-size:1.05rem">Plan with your own data</div>', unsafe_allow_html=True)
        ups = st.file_uploader("Drop a zip, an Excel workbook or the CSV files (add config.json for brand settings)",
                               type=["zip", "csv", "xlsx", "xlsm", "json"], accept_multiple_files=True, key="ups")
        st.toggle("Standardise POS / ERP exports (any column names, Indian dates, ₹ amounts)", True, key="standardise",
                  help="Recognises each file, maps its columns to the standard sheets with your saved mapping profiles, "
                       "cleans dates, numbers and codes, and checks data quality. Turn off only for files already in the template.")
        brand = st.selectbox("Business type", ["Standard settings"] + [b.stem for b in BRANDS], key=f"brand_{key}",
                             format_func=lambda x: PROFILE_NAMES.get(x, x.replace("_", " ").title()),
                             help="Starting settings for your kind of business: cover days, when stock counts as old, "
                                  "expiry window, handling cost. Fine-tune them under Settings.")
        base = schema.load_config(HERE / "brands" / f"{brand}.json" if brand != "Standard settings" else None, fb.load_settings())
        with st.expander("⚙️ Settings"):
            settings_panel(base)
        if st.button("▶️  Build my plan", type="primary", disabled=not ups, width="stretch", key=f"go_{key}"):
            start(None, base, ", ".join(f.name for f in ups)[:120])
        if not IN_BROWSER and st.button("🔎  Check my files first", disabled=not ups, width="stretch", key=f"onb_{key}",
                                        help="The data-onboarding agent reads the files without planning and explains what is "
                                             "missing or unclear. Its suggestions appear under Agents."):
            import ops_agents as OA
            tmp_ = Path(tempfile.mkdtemp(prefix="stockflow_onb_in_"))
            try:
                unpack(ups, tmp_)
                import daily
                daily._unzip(tmp_)
                ids_, rep_ = OA.run_onboard(tmp_, USER, SECRETS, cfg=base, label=", ".join(f.name for f in ups)[:60])
                import agents as AG
                st.text(AG.drafts(None).set_index("id").body.get(ids_[0], ""))
                if len(ids_) > 1:
                    st.info(f"{len(ids_) - 1} column-mapping suggestion(s) are waiting under Agents.")
            finally:
                shutil.rmtree(tmp_, ignore_errors=True)
    if not IN_BROWSER:
        if key == "results":                               # already inside an expander: no nesting
            with st.container(border=True):
                st.markdown("**📷 Stock from photos or scans**")
                stock_from_images(key)
        else:
            with st.expander("📷  Stock from photos or scans"):
                stock_from_images(key)


def demo_button(key, label="✨  Try the demo brand", primary=True, general=False):
    src = DEMO2 if general else DEMO
    if st.button(label, type="primary" if primary else "secondary", width="stretch", key=f"demo_{key}{'_g' if general else ''}",
                 disabled=not src.exists()):
        start_demo(general)


def start_demo(general=False):
    if general:
        start(DEMO2, schema.load_config(HERE / "brands" / "demo_general.json", fb.load_settings()), "Demo Mart (fictional general retailer)")
    else:
        start(DEMO, schema.load_config(HERE / "brands" / "demo.json", fb.load_settings()), "Demo Threads (fictional brand)")


def reopen(plan_id):
    """Rebuild a saved plan after a restart: same input pack + same settings give the same plan (the engine is deterministic)."""
    cached = pipeline.cache_get(plan_id)
    if cached is not None:                       # another session already has it open: share it, no rebuild
        row_ = plans.get(plan_id)
        finish_plan(dict(R=cached, plan_id=plan_id), str(row_.data_label) if row_ is not None else plan_id, reopen=plan_id)
        return
    busy_ = jobs.active_for(plan_id)
    if busy_:                                    # someone is already rebuilding it: wait for that job instead of a second build
        st.session_state.job = dict(id=busy_, label=f"Reopening {plan_id}", reopen=plan_id)
        st.rerun()
    row = plans.get(plan_id)
    if row is None:
        st.error("That plan is no longer in the database.")
        return
    cfg_ = row.settings or schema.load_config()
    if row.source == "demo":
        start(DEMO2 if "Demo Mart" in str(row.data_label) else DEMO, cfg_, row.data_label, reopen=plan_id)
        return
    if row.input_pack is None:
        st.error("The input files of that plan are no longer stored (too large, or past the retention period), so it can't be rebuilt. "
                 "Its lines, decisions and audit history are still kept.")
        return
    handed = False
    tmp = Path(tempfile.mkdtemp(prefix="stockflow_re_"))
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(row.input_pack))) as z:
            for m in z.infolist():
                p_ = Path(m.filename)
                if m.is_dir() or p_.is_absolute() or ".." in p_.parts:
                    continue
                (tmp / p_.name).write_bytes(z.read(m))
        for zf in list(tmp.glob("*.zip")):                 # a zip that was uploaded as such
            with zipfile.ZipFile(zf) as z:
                for m in z.infolist():
                    p_ = Path(m.filename)
                    if m.is_dir() or p_.is_absolute() or ".." in p_.parts or p_.suffix.lower() not in (".csv", ".xlsx", ".xlsm"):
                        continue
                    out = tmp / "zip" / p_
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(m))
            zf.unlink()
        for j in tmp.glob("*.json"):
            j.unlink()
        handed = True                                       # the background job reads the folder later and deletes it
        start(tmp, cfg_, row.data_label, reopen=plan_id, cleanup=tmp)
    finally:
        if not handed:
            shutil.rmtree(tmp, ignore_errors=True)


def recent_plans(key):
    try:
        rp = plans.recent(8, "guest") if GUEST else plans.recent(8)
    except Exception:  # noqa: BLE001
        return
    if rp.empty:
        return
    sec("Pick up where you left off")
    st.caption("Plans are saved with their input files, so they can be reopened after the app restarts, with every decision intact.")
    for r_ in rp.itertuples():
        a, b = st.columns([5, 1.3])
        a.markdown(f"**{r_.plan_date}** · {html.escape(str(r_.brand))} · {r_.trips} trips · built by {r_.created_by} "
                   f"{pd.Timestamp(r_.created_at):%d %b %H:%M}  \n<span style='color:{MUTED};font-size:.85rem'>{html.escape(str(r_.data_label))}</span>",
                   unsafe_allow_html=True)
        can_re = bool(getattr(r_, "has_inputs", True)) or pipeline.cache_get(r_.plan_id) is not None
        if b.button("Reopen" if can_re else "Inputs removed", key=f"re_{key}_{r_.plan_id}", width="stretch", disabled=not can_re,
                    help=None if can_re else "Its input files are no longer stored (too large, or past the retention period). "
                                             "Its lines, decisions and audit history are still in the database."):
            reopen(r_.plan_id)


def whatsapp_panel(released, approved):
    """WhatsApp alerts after release: sent automatically when the Business API is set up, else one tap per person."""
    W = notify.configured(st.secrets if hasattr(st, "secrets") else None)
    sec("💬 WhatsApp alerts")
    if released is None:
        st.caption("When you release, area managers, stores and warehouses with a WhatsApp number get their part of the plan."
                   + (" Sending is automatic (WhatsApp Business API is set up)." if W else " You send each message with one tap."))
        preview = notify.recipients(R, approved)
        if preview:
            with st.expander(f"Preview · {len(preview)} people will be told"):
                for rec in preview:
                    st.markdown(f"**{html.escape(str(rec['who']))}** · {html.escape(str(rec['role']))}  \n<span style='color:{MUTED}'>{html.escape(rec['text'])}</span>",
                                unsafe_allow_html=True)
    else:
        recs = notify.recipients(R, released)
        if not recs:
            note("Nobody to alert: add a <b>whatsapp</b> column to managers.csv, stores.csv or warehouses.csv "
                 "(numbers like +91 98765 43210). You can still send a test below.", "ok")
        elif W:
            if not st.session_state.get("wa_sent"):
                with st.spinner(f"Sending {len(recs)} WhatsApp messages…"):
                    st.session_state.wa_result = notify.send_all(W, recs, USER)
                st.session_state.wa_sent = True
                res_ = st.session_state.wa_result
                log("whatsapp.send", st.session_state.get("run_id", ""), kind="release", sent=int((res_.status == "sent").sum()), total=len(res_))
            res = st.session_state.get("wa_result", pd.DataFrame())
            okn = int((res.status == "sent").sum()) if len(res) else 0
            (st.success if okn == len(res) else st.warning)(f"WhatsApp: {okn} of {len(res)} messages sent.")
            if len(res):
                st.dataframe(res[["to", "role", "number", "status", "error"]], hide_index=True, width="stretch")
            if okn < len(res) and st.button("🔁 Try the failed ones again"):
                failed = set(res[res.status != "sent"].to)
                st.session_state.wa_result = notify.send_all(W, [r_ for r_ in recs if r_["who"] in failed], USER)
                st.rerun()
        else:
            st.caption("Tap to open WhatsApp with the message ready, then press send.")
            for i, rec in enumerate(recs):
                with st.container(border=True):
                    a, b = st.columns([4, 1.4])
                    a.markdown(f"**{rec['who']}** · {rec['role']} · …{rec['phone'][-4:]}  \n"
                               f"<span style='color:{MUTED};font-size:.88rem'>{html.escape(rec['text'])}</span>", unsafe_allow_html=True)
                    b.link_button("Send on WhatsApp", notify.wa_link(rec["phone"], rec["text"]), width="stretch")
    with st.expander("Send a test message to my own number"):
        num = st.text_input("Your WhatsApp number", placeholder="+91 98765 43210", key="wa_test")
        ph = notify.phone(num, str(cfg.get("whatsapp", {}).get("default_country_code", "91")))
        sample = notify.recipients(R, approved)
        text = sample[0]["text"] if sample else (f"StockFlow · {cfg['brand']['name']}: plan for {pl.run:%d %b} approved: "
                                                 f"{len(tr)} trips, {int(tr.pieces.sum()):,} pcs. Open StockFlow for details.")
        if num and not ph:
            st.error("That doesn't look like a mobile number.")
        elif ph:
            st.link_button("Open WhatsApp with a sample alert", notify.wa_link(ph, text), width="stretch")
    if not W:
        with st.expander("Send automatically (WhatsApp Business API)"):
            st.markdown("Add this to the app's **Settings → Secrets** and alerts go out by themselves on release:")
            st.code('''[whatsapp]
provider = "meta"                 # Meta WhatsApp Cloud API
token = "EAAG..."                 # permanent system-user token
phone_number_id = "1234567890"
template = "stockflow_approved"   # approved utility template: {{1}} = name, {{2}} = summary
language = "en"
# or: provider = "twilio", account_sid, auth_token, from = "whatsapp:+1...", content_sid''', language="toml")
            st.caption("WhatsApp only lets a business start a chat with an approved template. Numbers are used only for these "
                       "alerts; the send log keeps the last 4 digits.")
    lg = notify.log()
    if len(lg):
        with st.expander(f"Alert history ({len(lg)})"):
            st.dataframe(lg[::-1], hide_index=True, width="stretch")


# ============================================================================= landing
if "R" not in st.session_state and st.session_state.pop("auto_demo", False) and DEMO2.exists():
    css()
    with st.spinner("Building the demo plan…"):
        start_demo(general=True)
if "R" not in st.session_state:
    css()
    topbar(schema.DEFAULTS, "Demo" if OPEN or IN_BROWSER else USER)
    account_bar()
    job_panel()
    if st.session_state.get("job_note"):
        note(bold(st.session_state.pop("job_note")).replace("\n", "<br>"))
    st.markdown("""<div class="hero"><h1>The right stock in the right place, every morning.</h1>
<p>Upload yesterday's sales and stock from your POS and ERP, or photograph a count sheet. StockFlow gives you a short list of transfers between your warehouses and stores to approve, with what each one earns and what it costs to move.</p>
<div class="meta"><span>🏬 Stores, warehouses & franchises</span><span>📦 Apparel, electronics, grocery, pharmacy, spares</span><span>🏭 2–500 locations</span><span>📱 Web, Android & iOS</span></div></div>""",
                unsafe_allow_html=True)
    if OPEN:
        note("🔓 <b>Open demo:</b> no sign-in is set up yet. Use the demo brand, or add users before uploading real data.")
    if IN_BROWSER:
        note("🔒 This copy runs entirely in your browser: files you upload never leave this device. Ratings and receipts last until you close the page.", "ok")
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<div class="sf-h" style="font-weight:800;font-size:1.05rem;margin-bottom:.3rem">See it in 20 seconds</div>'
                    '<p style="color:#5E6875;margin:0 0 .7rem">Two fictional businesses, each with two warehouses.</p>',
                    unsafe_allow_html=True)
        a_, b_ = st.columns(2)
        with a_:
            demo_button("hero", "🛒  General retail demo", general=True)
            st.caption("10 stores · electronics, home, grocery with expiry dates")
        with b_:
            demo_button("hero", "👗  Apparel demo", primary=False)
            st.caption("9 stores · sizes, colours, festival season")
        st.markdown("""<div style="margin-top:1rem;display:grid;gap:.8rem">
<div class="step"><b class="n">1</b><div><b>Send your exports as they are</b><br><span style="color:#5E6875">Locations, products, sales and stock from any POS or ERP, or photos of count sheets and barcodes.</span></div></div>
<div class="step"><b class="n">2</b><div><b>Get priced transfers</b><br><span style="color:#5E6875">Refills, stock about to expire or sitting idle moved to where it sells, capped returns to the warehouse.</span></div></div>
<div class="step"><b class="n">3</b><div><b>Approve by region, ship, track</b><br><span style="color:#5E6875">On the web or the phone app; orders go to your warehouse system and receipts come back.</span></div></div></div>""",
                    unsafe_allow_html=True)
    with right:
        data_panel("landing")
    recent_plans("landing")
    sec("Why teams use it")
    st.markdown("""<div class="grid">
<div class="card"><div class="ic">💰</div><h4>Every move is priced</h4><p>Expected extra sales against freight and handling. Moves that don't pay are left out.</p></div>
<div class="card"><div class="ic">🧩</div><h4>Sizes, batches, expiry</h4><p>Apparel size sets stay whole; food and medicines near expiry go where they sell in time; expired stock never moves.</p></div>
<div class="card"><div class="ic">🚚</div><h4>Fewer, fuller trips</h4><p>One line per trip says why it goes. No more hundreds of single-unit transfers.</p></div>
<div class="card"><div class="ic">🎉</div><h4>Knows your season</h4><p>Peak days, festivals and sale weeks by region, from your own calendar.</p></div></div>""", unsafe_allow_html=True)
    with st.expander("📄 What data do I need?"):
        data_guide("landing")
    st.stop()

# ============================================================================= results
R = st.session_state.R
cfg = R["cfg"]
css(cfg["brand"].get("primary_color", TEAL) if cfg["brand"].get("primary_color", "").startswith("#") else TEAL)
M = lambda v: html.escape(E.money(v, cfg))
pl, mv, ex, val, tr, O = R["pl"], R["moves"], R["issues"], R["checks"], R["trips"], R["opportunity"]
P, g, stores = R["P"], pl.g, pl.st
SCOPE = st.session_state.get("scope") or []
if SCOPE:  # a user limited to some stores sees only their trips
    mv = mv[mv.from_id.isin(SCOPE) | mv.to_id.isin(SCOPE)]
    tr = tr[tr.from_id.isin(SCOPE) | tr.to_id.isin(SCOPE)]
L = ins.leadership(R)
topbar(cfg, f"{USER} · {ROLE}")
account_bar()
job_panel()
if st.session_state.get("job_note"):
    note(bold(st.session_state.pop("job_note")).replace("\n", "<br>"))
if st.session_state.get("save_error"):
    note(f"⚠️ This plan could not be saved to the database ({html.escape(st.session_state.save_error)}). It works for this session only.")
allp = bool((val.status == "PASS").all())
st.markdown(f"""<div class="hero sm"><h1>Plan for {pl.run:%A, %d %b %Y}</h1>
<p>{html.escape(cfg['brand']['name'])} · stock as of {P['today']:%d %b} · {len(stores)} stores · warehouses {html.escape(', '.join(pl.whs))}</p>
<div class="meta"><span>🚚 {len(tr)} trips</span><span>💰 net {html.escape(M(O['net']))}</span>
<span>{'✅ all ' + str(len(val)) + ' rules passed' if allp else '⚠️ some rules failed'}</span></div></div>""", unsafe_allow_html=True)
if OPEN:
    note("🔓 <b>Open demo:</b> anyone with the link can see this. Set up sign-in before using real data.")
with st.expander("📂  New data or settings"):
    data_panel("results")
    a_, b_ = st.columns(2)
    with a_:
        demo_button("results", "🛒  General retail demo", primary=False, general=True)
    with b_:
        demo_button("results", "👗  Apparel demo", primary=False)
    recent_plans("results")

TAB_NAMES = ["Overview", "Inventory", "KPIs", "Money", "Trips", "Approve", "Issues", "Stores", "Track", "Ask", "Feedback", "Data", "How it works"]
if not GUEST and not IN_BROWSER:                 # live operations data is never shown to guests
    TAB_NAMES[8:8] = ["Operations", "Exceptions"]
if not GUEST and not IN_BROWSER:                 # agents read live operations data and need the database
    TAB_NAMES.insert(TAB_NAMES.index("Ask") + 1, "Agents")
if can_do("settings") and not GUEST and not IN_BROWSER:
    TAB_NAMES.append("Integrations")
if can_do("audit"):
    TAB_NAMES.append("Audit")
if can_do("users"):
    TAB_NAMES.append("Admin")
TABS = dict(zip(TAB_NAMES, st.tabs(TAB_NAMES)))
T = [TABS[n] for n in ["Overview", "Money", "Trips", "Approve", "Issues", "Stores", "Track", "Ask", "Feedback", "Data", "How it works"]]

def erp_on():
    if os.environ.get("STOCKFLOW_ERP_CHANNEL"):
        return True
    return bool(secret("erp"))


# ---- overview
with T[0]:
    kc = [TEAL, PLUM, RED, SAFFRON]
    kpis([(l, v, s, kc[i % 4]) for i, (l, v, s) in enumerate(L["kpis"])])
    left, right = st.columns([3, 2], gap="large")
    with left:
        sec("What matters today")
        cols = ["#C2413A", TEAL, "#7D8792", SAFFRON, PLUM, "#B5652A"]
        for i, (_, text) in enumerate(L["insights"]):
            st.markdown(f'<div class="ins" style="--c:{cols[i % len(cols)]}"><span class="dot"></span><div>{bold(text)}</div></div>', unsafe_allow_html=True)
    with right:
        sec("Decisions for you")
        items = L["decisions"] or ["Nothing beyond approving today's trips."]
        st.markdown('<div class="card">' + "".join(f'<div style="display:flex;gap:.5rem;margin:.35rem 0"><span>☐</span><span>{bold(d)}</span></div>'
                                                   for d in items) + "</div>", unsafe_allow_html=True)
        d = mv.groupby("group").qty.sum().reindex(E.GROUPS).dropna()
        if len(d):
            sec("Pieces by reason")
            mx = d.max()
            st.markdown('<div class="card">' + "".join(
                f'<div style="margin:.45rem 0"><div style="display:flex;justify-content:space-between;font-size:.88rem"><span>{GICON[k]} {k}</span>'
                f'<b>{int(v):,}</b></div><div class="bar" style="--c:{GCOL[k]}"><i style="width:{100 * v / mx:.1f}%"></i></div></div>'
                for k, v in d.items()) + "</div>", unsafe_allow_html=True)
    if R["xlsx"] and Path(R["xlsx"]).exists():
        st.download_button("⬇️ Full plan (Excel)", Path(R["xlsx"]).read_bytes(), Path(R["xlsx"]).name, key="xl1", width="stretch")

# ---- money
with T[1]:
    sec("Every move earns more than it costs, or it's left out")
    kpis([("Gained", M(O["benefit"]), "extra margin + losses avoided", GREEN), ("Moving cost", M(O["move_cost"]), "freight, handling, trips", RED),
          ("Net gain", M(O["net"]), f"{O['net'] / max(O['move_cost'], 1):.0f}× the cost", TEAL),
          ("Saved by skipping", M(max(O["skipped_cost"] - O["skipped_benefit"], 0)), f"{len(R['skipped']) - O['held_small']} moves not worth it", SAFFRON)])
    by = O["by_group"]
    if len(by):
        d = by.melt("group", ["benefit", "move_cost"], var_name="what", value_name="amount")
        d["what"] = d["what"].map({"benefit": "Gained", "move_cost": "Moving cost"})
        fig = px.bar(d, x="amount", y="group", color="what", barmode="group", orientation="h", title="Gain vs cost by reason",
                     labels={"group": "", "amount": cfg["brand"]["currency"]}, color_discrete_map={"Gained": TEAL, "Moving cost": RED})
        plot(fig, 90 + 46 * len(by))
    sec("The cost of doing nothing")
    st.markdown(f"""<div class="grid">
<div class="card"><div class="ic">⏸️</div><h4>Skip today's trips</h4><p>The brand gives up about <b>{M(O['benefit'])}</b>: lost margin on stock-outs, plus markdown and holding cost on old stock.</p></div>
<div class="card"><div class="ic">🛒</div><h4>Still at risk</h4><p><b>{M(O['still_at_risk'])}</b> of margin where no safe stock exists anywhere. That's the case for re-ordering.</p></div>
<div class="card"><div class="ic">🕰️</div><h4>Old stock sitting</h4><p>{O['aged_left_pieces']:,} old pieces in stores cost about <b>{M(O['aged_cost_per_month'])} a month</b> to keep.</p></div></div>""",
                unsafe_allow_html=True)
    with st.expander("How the money is worked out"):
        st.markdown("Expected extra pieces sold (demand over lead time + cover, minus what the giving store would have sold) × margin "
                    "(net price after real discount and tax − cost). Old stock also earns the markdown and holding cost it avoids. "
                    "Costs are freight per piece and per trip from lanes.csv, plus handling per piece. All figures are estimates.")
    sk = R["skipped"]
    if len(sk):
        with st.expander(f"Left out as not worth moving · {len(sk)} lines"):
            st.dataframe(sk[["group", "from_id", "to_id", "style_name", "size", "qty", "benefit", "move_cost", "skip_why"]].round(0),
                         hide_index=True, width="stretch",
                         column_config={"from_id": "from", "to_id": "to", "style_name": "item", "move_cost": "cost", "skip_why": "why"})

# ---- trips
with T[2]:
    kpis([("Trips", f"{len(tr)}", f"{int(tr.from_id.isin(pl.whs).sum())} from warehouses"), ("Pieces", f"{int(mv.qty.sum()):,}", M(mv.value_mrp.sum()) + " at MRP"),
          ("Urgent", f"{int((tr.urgent > 0).sum())}", "a store runs out first", RED), ("Need sign-off", f"{int(tr.needs_approval.sum())}", "high value", PLUM)])
    groups = [x for x in E.GROUPS if x in set(tr.main_group)]
    a, b = st.columns([3, 2])
    pick = a.pills("Reason", ["All"] + groups, default="All", key="trip_grp", label_visibility="collapsed")
    q = b.text_input("Search", placeholder="🔍 Store, city or TO number", label_visibility="collapsed", key="trip_q")
    show = tr if pick in (None, "All") else tr[tr.main_group == pick]
    if q:
        ql = q.lower()
        city = lambda x: stores.city.get(x, x)
        show = show[[ql in " ".join([r.to_number, r.from_id, r.to_id, str(city(r.from_id)), str(city(r.to_id))]).lower() for r in show.itertuples()]]
    st.caption(f"{len(show)} trips · tap one to see it size by size")
    for r in show.head(80).itertuples():
        chips = (f'<span class="chip" style="color:{RED}">urgent</span> ' if r.urgent else "") + \
                (f'<span class="chip" style="color:{PLUM}">sign-off</span> ' if r.needs_approval else "") + \
                f'<span class="chip" style="color:{GCOL.get(r.main_group, TEAL)}">{GICON.get(r.main_group, "")} {html.escape(r.main_group)}</span>'
        st.markdown(f'<div class="trip"><div class="r"><span class="route">{html.escape(r.from_id)} → {html.escape(r.to_id)}</span>'
                    f'<span class="to">{r.to_number}</span>{chips}<span class="nums"><b>{r.pieces:,} pcs</b> · net {M(r.net_benefit)} · arrives {r.arrives:%d %b}</span></div>'
                    f'<div class="why">{html.escape(r.reason)}</div></div>', unsafe_allow_html=True)
        with st.expander(f"Show sizes · {r.to_number}"):
            lines = mv[mv.trip_key == r.trip_key][["group", "style_name", "colour", "size", "qty", "net_benefit", "reason"]].round(0)
            st.dataframe(lines, hide_index=True, width="stretch",
                         column_config={"style_name": "item", "net_benefit": st.column_config.NumberColumn("net", format="%d"),
                                        "reason": st.column_config.TextColumn("why", width="large")})
    if len(show) > 80:
        st.caption(f"Showing 80 of {len(show)} trips. Narrow with the reason filter or search.")
    if len(R["alerts"]):
        sec("Area manager alerts")
        for name, grp in R["alerts"].groupby("manager"):
            with st.expander(f"👤 {name} · {len(grp)} alerts"):
                for r in grp.itertuples():
                    st.markdown(f"{r.rank}. {r.alert}")

# ---- approve
with T[3]:
    can_ok = can_do("approve")
    pack = E.approval_pack(tr, cfg)
    sec(f"Approve the day in {len(pack)} decisions")
    st.caption(f"They cover all {len(tr)} trips. Everything starts switched on; switch off what you don't want."
               + ("" if can_ok else " Your role can view but not approve."))
    ok_to = set()
    step_names = {"1": "Urgent trips", "2": "Needs sign-off (high value)", "3": "Routine, by reason"}
    for step, grp in pack.groupby("step", sort=True):
        n = step.split(" ")[0]
        sec(f"{n} · {step_names.get(n, step)}")
        for i, r in enumerate(grp.itertuples()):
            with st.container(border=True):
                a, b = st.columns([5, 2])
                on = a.toggle(f"**{r.decision}**", value=True, key=f"ap_{step}_{i}", disabled=not can_ok)
                a.markdown(f'<div style="color:{MUTED};font-size:.88rem;line-height:1.45">{html.escape(r.detail)}</div>', unsafe_allow_html=True)
                b.markdown(f'<div style="text-align:right;font-variant-numeric:tabular-nums"><b>{r.pieces:,} pcs</b><br>'
                           f'<span style="color:{MUTED};font-size:.85rem">{r.trips} trip{"s" if r.trips > 1 else ""} · net {M(r.net_benefit)}</span></div>',
                           unsafe_allow_html=True)
                if on:
                    ok_to.update(r.to_numbers.split(","))
    PLAN_ID = st.session_state.get("run_id")
    saved = bool(PLAN_ID) and not st.session_state.get("save_error") and not IN_BROWSER
    # ---- line by line (approve / reject / change quantity), saved in the database with who and when
    dec = plans.decisions(PLAN_ID) if saved else pd.DataFrame()
    n_rej = int((dec.decision == "rejected").sum()) if len(dec) else 0
    n_edit = int(((dec.approved_qty < dec.qty) & (dec.decision != "rejected")).sum()) if len(dec) else 0
    with st.expander(f"✏️  Line by line: approve, reject or change quantities · {n_rej} rejected · {n_edit} changed", expanded=False):
        if not saved:
            st.info("Line decisions need the plan to be saved in the database." if not IN_BROWSER else "Not available in the browser-only copy.")
        else:
            st.caption("Lines you reject or reduce here are taken out of the transfer orders. A quantity can be lowered but not raised: "
                       "the recommended quantity is the most the rules allow. Every change is saved with your name and the time.")
            view = dec.merge(tr[["to_number", "from_id", "to_id"]].rename(columns={"from_id": "f", "to_id": "t"}), on="to_number", how="inner")
            a, b, c = st.columns([2, 2, 2])
            trips_opts = ["All trips"] + list(tr.to_number)
            pick_to = a.selectbox("Trip", trips_opts, format_func=lambda x: x if x == "All trips" else
                                  f"{x} · {tr.set_index('to_number').at[x, 'from_id']} → {tr.set_index('to_number').at[x, 'to_id']}", key="ln_trip")
            pick_grp = b.selectbox("Reason", ["All reasons"] + [g_ for g_ in E.GROUPS if g_ in set(view.grp)], key="ln_grp")
            q_ = c.text_input("Search", placeholder="SKU, store or item", key="ln_q")
            if pick_to != "All trips":
                view = view[view.to_number == pick_to]
            if pick_grp != "All reasons":
                view = view[view.grp == pick_grp]
            if q_:
                ql = q_.lower()
                view = view[view.apply(lambda r: ql in f"{r.sku_id} {r.from_id} {r.to_id} {r.style_name}".lower(), axis=1)]
            st.caption(f"{len(view):,} lines" + (" · showing the first 400; narrow the filter to see the rest" if len(view) > 400 else ""))
            view = view.head(400)
            ed_in = pd.DataFrame({"move_id": view.move_id, "trip": view.to_number, "from": view.from_id, "to": view.to_id, "item": view.style_name,
                                  "size": view["size"], "recommended": view.qty.astype(int), "approve": view.decision != "rejected",
                                  "qty": view.approved_qty.fillna(view.qty).astype(int), "note": view.note.fillna(""),
                                  "net": view.net_benefit.round(0), "why": view.reason, "by": view.decided_by.fillna("")})
            ed = st.data_editor(ed_in, hide_index=True, width="stretch", key=f"ln_ed_{PLAN_ID}_{pick_to}_{pick_grp}_{q_}",
                                disabled=["move_id", "trip", "from", "to", "item", "size", "recommended", "net", "why", "by"] if can_ok else True,
                                column_config={"move_id": None, "approve": st.column_config.CheckboxColumn("✓"),
                                               "qty": st.column_config.NumberColumn("qty", min_value=0, step=1),
                                               "why": st.column_config.TextColumn(width="large"), "net": st.column_config.NumberColumn(format="%d")})
            if can_ok:
                a, b, c = st.columns(3)
                if a.button("💾 Save line changes", type="primary", width="stretch", key="ln_save"):
                    ch = pd.DataFrame({"move_id": ed.move_id, "decision": ed.approve.map({True: "approved", False: "rejected"}),
                                       "approved_qty": ed.qty.fillna(0).astype(int), "note": ed.note})
                    over = ed[ed.qty > ed.recommended]
                    changed = plans.set_decisions(PLAN_ID, ch, USER)
                    for c_ in changed:
                        log("decision.line", f"{PLAN_ID}/{c_['move_id']}", **c_)
                    if len(over):
                        st.warning(f"{len(over)} quantities were above the recommendation and were capped at it.")
                    st.success(f"Saved {len(changed)} changed lines.")
                    st.rerun()
                if b.button("Reject all lines shown", width="stretch", key="ln_rej"):
                    ch = pd.DataFrame({"move_id": ed.move_id, "decision": "rejected", "approved_qty": 0, "note": ed.note})
                    changed = plans.set_decisions(PLAN_ID, ch, USER)
                    log("decision.bulk", PLAN_ID, decision="rejected", lines=len(changed), filter=f"{pick_to}|{pick_grp}|{q_}")
                    st.rerun()
                if c.button("Reset lines shown to recommended", width="stretch", key="ln_reset"):
                    ch = pd.DataFrame({"move_id": ed.move_id, "decision": "approved", "approved_qty": ed.recommended, "note": ""})
                    changed = plans.set_decisions(PLAN_ID, ch, USER)
                    log("decision.bulk", PLAN_ID, decision="reset", lines=len(changed), filter=f"{pick_to}|{pick_grp}|{q_}")
                    st.rerun()
    orders = R["orders"]
    orders = orders[orders.to_number.isin(ok_to) & orders.to_number.isin(tr.to_number)] if len(orders) else orders
    if saved:
        orders = plans.apply_decisions(orders, PLAN_ID)
        try:
            qrej = db.read("trip_decisions", {"plan_id": PLAN_ID})
            qrej = set(qrej[qrej.decision == "rejected"].to_number) if len(qrej) else set()
        except Exception:  # noqa: BLE001
            qrej = set()
        if qrej and len(orders):
            orders = orders[~orders.to_number.isin(qrej)]
            st.caption(f"{len(qrej)} trips rejected in the regional queues (Operations tab) are left out.")
    t_ok = tr[tr.to_number.isin(set(orders.to_number))] if len(orders) else tr.iloc[0:0]
    with st.container(border=True):
        kpis([("Approved", f"{len(t_ok)} of {len(tr)} trips", f"{int(orders.qty.sum()) if len(orders) else 0:,} pieces in {len(orders):,} lines"),
              ("Changed lines", f"{n_rej + n_edit}", f"{n_rej} rejected · {n_edit} reduced", SAFFRON),
              ("Net gain", M(t_ok.net_benefit.sum()), "of approved trips, before line changes", GREEN)])
        if can_ok:
            a, b = st.columns(2)
            a.download_button("⬇️ Transfer orders (CSV)", safety.csv(orders), f"transfer_orders_{pl.run:%Y-%m-%d}.csv",
                              type="primary", width="stretch",
                              on_click=lambda: log("orders.download", PLAN_ID, orders=int(orders.to_number.nunique()), lines=len(orders)))
            if b.button("📤 Release & send WhatsApp alerts", width="stretch", disabled=orders.empty or not can_do("release")):
                skipped_ = fb.release_orders(orders, USER, st.session_state.run_id)
                if skipped_:
                    st.warning(f"{len(skipped_)} transfer order(s) were already released and were not released again: "
                               + ", ".join(skipped_[:5]) + ("…" if len(skipped_) > 5 else ""))
                    orders = orders[~orders.to_number.isin(skipped_)]
                log("orders.release", PLAN_ID, orders=int(orders.to_number.nunique()), lines=len(orders), pieces=int(orders.qty.sum()),
                    rejected_lines=n_rej, reduced_lines=n_edit, decisions_off=int(len(pack) - sum(1 for k_ in st.session_state
                                                                                                   if str(k_).startswith("ap_") and st.session_state[k_])))
                st.session_state.released = orders.copy()
                st.session_state.wa_sent = False
                import webhooks
                webhooks.emit("trips.released", {"plan_id": PLAN_ID, "to_numbers": sorted(orders.to_number.unique()),
                                                 "pieces": int(orders.qty.sum())})
                st.success(f"Released {orders.to_number.nunique()} transfer orders. Record receipts on the Track tab.")
                if erp_on():
                    ok_, m_ = erp.export_orders(orders, PLAN_ID, USER, SECRETS)
                    log("erp.export", PLAN_ID, ok=ok_, orders=int(orders.to_number.nunique()), message=m_[:200])
                    (st.success if ok_ else st.warning)(m_)
            if IN_BROWSER:
                with st.expander("Download blocked here? Copy the transfer orders instead"):
                    st.code(orders.to_csv(index=False), language=None)
            st.caption("One transfer order per trip, in a generic format most ERPs and warehouse systems can import.")
    if can_ok:
        whatsapp_panel(st.session_state.get("released"), orders)

# ---- issues
with T[4]:
    sev = {"High": ("🔴", RED), "Medium": ("🟡", SAFFRON), "Info": ("🔵", TEAL)}
    counts = ex.severity.value_counts()
    kpis([("Urgent", int(counts.get("High", 0)), "act today", RED), ("Worth a look", int(counts.get("Medium", 0)), "this week", SAFFRON),
          ("For information", int(counts.get("Info", 0)), "handled automatically", TEAL)])
    for kind, grp in ex.groupby("issue", sort=False):
        ic, col = sev.get(grp.severity.iloc[0], ("", TEAL))
        val_txt = f" · {M(grp.value.sum())}" if grp.value.sum() > 0 else ""
        with st.expander(f"{ic} {kind} · {len(grp)}{val_txt}"):
            st.markdown(f"**What StockFlow did:** {grp.action.iloc[0]}")
            st.dataframe(grp[["store", "sku", "detail"]], hide_index=True, width="stretch",
                         column_config={"detail": st.column_config.TextColumn(width="large")})
    sn = g[g.oh > 0].copy()
    if sn.age.notna().any():
        a = cfg["ageing"]["age_days"]
        edges = sorted({-1, 30, 90, int(a), 365, 100000})           # the "old" threshold depends on the business type
        names_ = {30: "1 month", 90: "3 months", 365: "1 year", int(a): f"{int(a)} days"}
        labels_ = [f"up to {names_[hi]}" if lo == -1 else (f"over {names_[lo]}" if hi == 100000 else f"{names_[lo]}–{names_[hi]}")
                   for lo, hi in zip(edges[:-1], edges[1:])]
        sn["band"] = pd.cut(sn.age, edges, labels=labels_)
        d = sn.groupby("band", observed=False).oh.sum().reset_index()
        plot(px.bar(d, x="band", y="oh", title="How long stock has been in stores", labels={"oh": "pieces", "band": ""},
                    color_discrete_sequence=[PLUM]), 280, False)

# ---- stores
with T[5]:
    allowed = [s for s in stores.index if not SCOPE or s in SCOPE]
    s = st.selectbox("Store", allowed, format_func=lambda x: f"{x} · {stores.city[x]} · tier {stores.tier[x]}")
    S = ins.store_summary(R).loc[s]
    kpis([("On hand + on the way", f"{S.pieces_now:,}", "before the plan"), ("Coming in", f"{S.coming_in:,}", "this plan", GREEN),
          ("Going out", f"{S.going_out:,}", "this plan", SAFFRON),
          ("Shelves used after", f"{S.fill_after:.0%}" if S.fill_after == S.fill_after else "—", "of capacity", PLUM)])
    for r in ex[ex.store == s].itertuples():
        st.warning(f"**{r.issue}:** {r.detail} → {r.action}")
    gs = g.loc[s]
    opt = gs.groupby("option").agg(sizes=("pos", lambda x: int((x > 0).sum())), made=("size", "count"), sells=("fd30", "sum"))
    opt["missing core"] = gs[gs.is_core & (gs.pos <= 0)].groupby("option").size().reindex(opt.index).fillna(0).astype(int)
    brk = opt[(opt.sizes >= 2) & (opt["missing core"] > 0)].sort_values("sells", ascending=False)
    a, b = st.columns(2, gap="large")
    with a:
        cov = (gs.pos / gs.fd30).where(gs.fd30 > 0.01)
        band = pd.cut(cov, [-1, 7, 14, 30, 60, 1e9], labels=["< 1 wk", "1–2 wk", "2–4 wk", "1–2 mo", "2+ mo"])
        d = band.value_counts().reindex(band.cat.categories).reset_index()
        d.columns = ["lasts", "sizes"]
        plot(px.bar(d, x="lasts", y="sizes", title="How long each size lasts (before plan)", color_discrete_sequence=[TEAL]), 280, False)
    with b:
        sec(f"🧩 {len(brk)} selling options missing a core size")
        if len(brk):
            st.dataframe(brk.round(2).rename(columns={"sizes": "in stock", "sells": "pcs/day"}), width="stretch", height=230)
    for title, df in (("⬇️ Coming in", tr[tr.to_id == s]), ("⬆️ Going out", tr[tr.from_id == s])):
        with st.expander(f"{title} · {len(df)} trips"):
            for r in df.itertuples():
                st.markdown(f"**{html.escape(str(r.to_number))}** {html.escape(str(r.from_id))} → {html.escape(str(r.to_id))} · {r.pieces} pcs  \n<span style='color:{MUTED}'>{html.escape(r.reason)}</span>",
                            unsafe_allow_html=True)

# ---- track
SCOL = {"Waiting to leave": MUTED, "Late leaving": RED, "In transit": TEAL, "Late arriving": RED,
        "Received short": SAFFRON, "Received over": SAFFRON, "Received": GREEN}


def can_mark(loc):
    return can_do("track_all") or (can_do("track_own") and bool(SCOPE) and loc in SCOPE) or auth.can(ROLE, "approver")


def movement_alert(t, kind):
    """Tell the people at both ends; automatic with the WhatsApp API, else queue one-tap buttons."""
    recs = notify.movement_recipients(R, t, kind)
    if not recs:
        return
    W = notify.configured(st.secrets if hasattr(st, "secrets") else None)
    if W:
        res = notify.send_all(W, recs, USER)
        log("whatsapp.send", t.to_number, kind=kind, sent=int((res.status == "sent").sum()), total=len(res))
        st.toast(f"WhatsApp: {int((res.status == 'sent').sum())} of {len(res)} alerts sent")
    else:
        st.session_state.mv_alerts = recs


def step_html(label, when, state):
    col = {"done": GREEN, "late": RED, "todo": "#C9C4BB"}[state]
    w = html.escape(str(when)) if when else ("overdue" if state == "late" else "pending")
    return (f'<div style="display:flex;gap:.45rem;align-items:flex-start;min-width:0"><span style="flex:none;width:12px;height:12px;'
            f'border-radius:50%;margin-top:.3rem;background:{col}"></span><div style="line-height:1.25"><b style="font-size:.86rem">{label}</b>'
            f'<br><span style="color:{MUTED};font-size:.78rem">{w}</span></div></div>')


with T[6]:
    if GUEST:
        st.info("Transfer tracking shows live data, so it needs an account. Sign in to follow released transfers.")
    else:
        tz = cfg["brand"].get("timezone", "Asia/Kolkata")
        sec("Where is the stock?")
        st.caption("Every released transfer order is followed from release, to leaving the sender, to arriving at the receiver. "
                   "Mark each step here, or upload files from your warehouse system.")
        if st.session_state.get("mv_alerts"):
            with st.container(border=True):
                st.markdown("**💬 Tell them on WhatsApp**")
                for rec in st.session_state.mv_alerts:
                    a, b = st.columns([4, 1.4])
                    a.markdown(f"**{rec['who']}** · {rec['role']}  \n<span style='color:{MUTED};font-size:.86rem'>{html.escape(rec['text'])}</span>",
                               unsafe_allow_html=True)
                    b.link_button("Send", notify.wa_link(rec["phone"], rec["text"]), width="stretch")
                if st.button("Done", key="mv_done"):
                    st.session_state.mv_alerts = None
                    st.rerun()
        tro, trl = fb.tracking()
        if tro.empty:
            st.info("Nothing to track yet. Approve and release trips on the Approve tab.")
        else:
            if SCOPE:
                tro = tro[tro.from_location.isin(SCOPE) | tro.to_location.isin(SCOPE)]
            cnt = tro.status.value_counts()
            kpis([("Not left yet", int(cnt.get("Waiting to leave", 0) + cnt.get("Late leaving", 0)), f"{int(cnt.get('Late leaving', 0))} late", MUTED),
                  ("On the road", int(cnt.get("In transit", 0) + cnt.get("Late arriving", 0)), f"{int(cnt.get('Late arriving', 0))} overdue", TEAL),
                  ("Arrived", int(cnt.get("Received", 0) + cnt.get("Received short", 0) + cnt.get("Received over", 0)),
                   f"{int(cnt.get('Received short', 0) + cnt.get('Received over', 0))} with a different count", GREEN),
                  ("Pieces still out", f"{int((tro.sent - tro.received).clip(lower=0)[tro.sent > 0].sum()):,}", "left but not received", SAFFRON)])
            groups = {"All": None, "Not left": ["Waiting to leave", "Late leaving"], "On the road": ["In transit", "Late arriving"],
                      "Arrived": ["Received", "Received short", "Received over"],
                      "Needs attention": ["Late leaving", "Late arriving", "Received short", "Received over"]}
            a, b = st.columns([3, 2])
            f = a.pills("Show", list(groups), default="All", key="trk_f", label_visibility="collapsed")
            q = b.text_input("Find", placeholder="🔍 TO number or store", key="trk_q", label_visibility="collapsed")
            view = tro if not groups.get(f or "All") else tro[tro.status.isin(groups[f])]
            if q:
                view = view[view.apply(lambda r: q.lower() in f"{r.to_number} {r.from_location} {r.to_location}".lower(), axis=1)]
            st.caption(f"{len(view)} transfer orders")
            for r in view.head(60).itertuples():
                left_state = "done" if r.sent_lines > 0 else ("late" if r.status == "Late leaving" else "todo")
                arr_state = "done" if r.got_lines > 0 else ("late" if r.status == "Late arriving" else "todo")
                got = f" · received {int(r.received):,}" if r.got_lines > 0 else ""
                sent = f" · sent {int(r.sent):,}" if r.sent_lines > 0 else ""
                days = f" · {r.days_on_road:g} days on the road" if r.days_on_road == r.days_on_road else ""
                with st.container(border=True):
                    st.markdown(
                        f'<div class="r" style="display:flex;flex-wrap:wrap;gap:.4rem .6rem;align-items:center"><b>{html.escape(r.from_location)} → '
                        f'{html.escape(r.to_location)}</b><span style="color:{MUTED};font-size:.8rem;font-weight:600">{r.to_number}</span>'
                        f'<span class="chip" style="color:{SCOL[r.status]}">{r.status}</span>'
                        f'<span style="margin-left:auto;font-size:.85rem;font-variant-numeric:tabular-nums">planned {int(r.planned):,}{sent}{got}{days}</span></div>'
                        f'<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.5rem;margin-top:.6rem">'
                        + step_html("Released", str(r.released_at)[:16], "done")
                        + step_html(f"Left {html.escape(r.from_location)}", r.left_at if r.sent_lines > 0 else f"due {pd.Timestamp(r.ship_date):%d %b}"
                                    if left_state != "late" else None, left_state)
                        + step_html(f"At {html.escape(r.to_location)}", r.arrived_at if r.got_lines > 0 else f"due {pd.Timestamp(r.expected):%d %b}"
                                    if arr_state != "late" else None, arr_state) + "</div>", unsafe_allow_html=True)
                    ln = trl[trl.to_number == r.to_number][["sku_id", "planned", "sent", "received"]]
                    if r.sent_lines == 0 and can_mark(r.from_location):
                        c1, c2 = st.columns([1, 1])
                        if c1.button("🚚 Mark as left", key=f"left_{r.to_number}", type="primary", width="stretch"):
                            fb.mark_dispatched(r.to_number, USER, tz=tz)
                            log("move.dispatched", r.to_number, qty=int(r.planned), route=f"{r.from_location}>{r.to_location}")
                            row = fb.tracking()[0].set_index("to_number").loc[r.to_number]
                            movement_alert(pd.Series({**row.to_dict(), "to_number": r.to_number}), "left")
                            st.rerun()
                        with c2.popover("Sent a different quantity?", width="stretch"):
                            ed = st.data_editor(ln[["sku_id", "planned"]].rename(columns={"planned": "sent"}), key=f"eds_{r.to_number}",
                                                hide_index=True, disabled=["sku_id"], width="stretch")
                            if st.button("Save what was sent", key=f"sv_s_{r.to_number}"):
                                fb.mark_dispatched(r.to_number, USER, dict(zip(ed.sku_id, ed.sent.astype(int))), "edited", tz)
                                log("move.dispatched", r.to_number, qty=int(ed.sent.sum()), planned=int(r.planned), edited=True)
                                row = fb.tracking()[0].set_index("to_number").loc[r.to_number]
                                movement_alert(pd.Series({**row.to_dict(), "to_number": r.to_number}), "left")
                                st.rerun()
                    elif r.sent_lines > 0 and r.got_lines == 0 and can_mark(r.to_location):
                        c1, c2 = st.columns([1, 1])
                        if c1.button("📦 Mark as received (all of it)", key=f"rcv_{r.to_number}", type="primary", width="stretch"):
                            fb.mark_received(r.to_number, USER, tz=tz)
                            log("move.received", r.to_number, qty=int(r.sent), route=f"{r.from_location}>{r.to_location}")
                            st.rerun()
                        with c2.popover("Received a different quantity?", width="stretch"):
                            ed = st.data_editor(ln[["sku_id", "sent"]].assign(received=ln.sent), key=f"edr_{r.to_number}",
                                                hide_index=True, disabled=["sku_id", "sent"], width="stretch")
                            if st.button("Save what arrived", key=f"sv_r_{r.to_number}"):
                                fb.mark_received(r.to_number, USER, dict(zip(ed.sku_id, ed.received.astype(int))), "counted", tz)
                                log("move.received", r.to_number, qty=int(ed.received.sum()), sent=int(r.sent), counted=True)
                                row = fb.tracking()[0].set_index("to_number").loc[r.to_number]
                                if int(row.received) != int(row.sent):
                                    movement_alert(pd.Series({**row.to_dict(), "to_number": r.to_number}), "received")
                                st.rerun()
                    elif r.status in ("Received short", "Received over"):
                        with st.expander("See the lines that differ"):
                            st.dataframe(ln[ln.received != ln.sent.where(ln.sent > 0, ln.planned)], hide_index=True, width="stretch")
            if len(view) > 60:
                st.caption(f"Showing 60 of {len(view)}. Use the filter or search.")
            st.download_button("⬇️ Tracking report (CSV)", safety.csv(trl), "stock_movement_tracking.csv", width="stretch")
        if can_do("track_all"):
            with st.expander("📥 Upload from your warehouse system (many orders at once)"):
                st.caption("Dispatch file: to_number, sku_id, qty_shipped (optional dispatched_on). "
                           "Receipt file: to_number, sku_id, qty_received (optional received_on).")
                a, b = st.columns(2)
                df_ = a.file_uploader("What left (CSV)", type=["csv"], key="up_disp")
                rf_ = b.file_uploader("What arrived (CSV)", type=["csv"], key="up_rcpt")
                if st.button("Record uploads", type="primary", disabled=not (df_ or rf_)):
                    try:
                        n1 = fb.add_dispatches(pd.read_csv(df_, dtype=str), USER) if df_ else 0
                        n2 = fb.add_receipts(pd.read_csv(rf_, dtype=str), USER) if rf_ else 0
                        log("move.upload", "tracking files", dispatch_lines=n1, receipt_lines=n2)
                        st.success(f"Recorded {n1} dispatch lines and {n2} receipt lines.")
                    except ValueError as e:
                        st.error(str(e))

# ---- ask
with T[7]:
    key = st.session_state.get("api_key")
    ai = chat.claude_available(key) and st.session_state.get("ai_ok", False)
    note(("🤖 <b>AI answers are on.</b>" if ai else "⚡ <b>Quick answers:</b> nothing leaves this server.")
         + " Ask about stores, trips, money or problems in this plan.", "ok")
    if not st.session_state.get("chat"):
        cols = st.columns(3)
        for i, qq in enumerate(["What's the plan worth?", "Which trips need sign-off?", "What problems did you find?"]):
            if cols[i].button(qq, width="stretch", key=f"qq{i}"):
                st.session_state.chat = [{"role": "user", "content": qq}, {"role": "assistant", "content": chat.quick_answer(qq, R)}]
                st.rerun()
    if can_do("settings") and not ai:
        with st.expander("Turn on AI answers"):
            st.caption("Sends the plan report (trips, moves, issues, stock figures; never customer or staff data) to the AI provider. "
                       "Check your data-processing agreement covers this first.")
            k = st.text_input("Anthropic API key (blank = use the server's key)", type="password")
            if st.checkbox("Sending the plan report to the AI provider is allowed") and st.button("Turn on"):
                if k:
                    st.session_state.api_key = k.strip()
                st.session_state.ai_ok = True
                log("ai.enable", "chat", own_key=bool(k))
                st.rerun()
    st.session_state.setdefault("chat", [])
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    q = st.chat_input("Ask about this plan…")
    if q:
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            if ai:
                st.session_state.setdefault("ctx", chat.build_context(R))
                a_ = chat.ask_claude(q, st.session_state.chat, st.session_state.ctx, cfg["brand"]["name"], key)
            else:
                a_ = chat.quick_answer(q, R)
            st.markdown(a_)
        st.session_state.chat += [{"role": "user", "content": q}, {"role": "assistant", "content": a_}]

# ---- feedback
with T[8]:
    runs, r = fb.runs(), fb.ratings()
    with st.form("rate", clear_on_submit=True, border=True):
        st.markdown('<div class="sf-h" style="font-weight:800;font-size:1.05rem">How was this plan?</div>', unsafe_allow_html=True)
        st.caption("Rate it once the stock has landed. Votes tune the settings for future runs.")
        stars = st.feedback("stars")
        c1, c2, c3 = st.columns(3)
        right = c1.select_slider("Moves were right", [1, 2, 3, 4, 5], 4)
        easy = c2.select_slider("Easy to understand", [1, 2, 3, 4, 5], 4)
        time_ = c3.select_slider("Saved time", [1, 2, 3, 4, 5], 4)
        changes = st.multiselect("What should change next time?", list(fb.SUGGESTIONS))
        comment = st.text_area("Anything else?")
        if st.form_submit_button("Send feedback", type="primary", width="stretch"):
            if stars is None:
                st.warning("Please pick a star rating.")
            else:
                fb.add_rating(st.session_state.run_id, stars + 1, right, easy, time_, changes, comment, USER)
                log("feedback.rate", st.session_state.run_id, stars=stars + 1)
                st.success("Thank you!")
    if not r.empty:
        kpis([("Average rating", f"{r.stars.mean():.1f} ★", f"{len(r)} reviews", SAFFRON), ("Moves right", f"{r.moves_right.mean():.1f}/5", ""),
              ("Easy", f"{r.easy.mean():.1f}/5", ""), ("Saves time", f"{r.saves_time.mean():.1f}/5", "")])
    sug = fb.suggested_settings(cfg)
    if len(sug):
        sec("Suggested settings from feedback")
        st.dataframe(sug.drop(columns="setting"), hide_index=True, width="stretch")
        if can_do("settings"):
            a, b = st.columns(2)
            if a.button("✅ Use these from the next run", type="primary", width="stretch"):
                over = fb.load_settings()
                for row in sug.itertuples():
                    s1, k1 = row.setting.split(".")
                    over.setdefault(s1, {})[k1] = int(row.suggested)
                fb.save_settings(over, USER)
                log("settings.accept", "overrides", **{k.replace(".", "_"): v for k, v in
                                                       {f"{a_}.{b_}": v for a_, d_ in over.items() for b_, v in d_.items()}.items()})
                st.success("Saved.")
            if b.button("↩️ Back to standard settings", width="stretch"):
                fb.reset_settings(USER)
                log("settings.reset", "overrides")
    if not runs.empty and not GUEST:
        with st.expander(f"Run history ({len(runs)})"):
            st.dataframe(runs[::-1], hide_index=True, width="stretch")

# ---- data
with T[9]:
    data_guide("tab")

def accuracy_panel():
    import accuracy
    sec("How accurate is the forecast?")
    st.caption("A back-test: the plan is rebuilt as if it were 4 weeks ago, and its forecast is compared with what then sold, only "
               "where the item was on the shelf. The simple 28-day average most chains use is shown for comparison. Error = WAPE "
               "(total absolute error ÷ units sold; lower is better).")
    rep = accuracy.load(PLAN_ID) if PLAN_ID and not IN_BROWSER else None
    if rep is None and st.button("📏 Measure it on this plan's data", key="acc_go"):
        row = plans.get(PLAN_ID) if PLAN_ID else None
        try:
            with st.spinner("Rebuilding the plan as of 4 weeks ago and comparing…"):
                if row is not None and row.source == "demo" or row is None:
                    src = DEMO2 if "Demo Mart" in str(st.session_state.get("label", "")) else DEMO
                    rep = accuracy.for_inputs(src, cfg)
                elif row.input_pack is not None:
                    tmpa = Path(tempfile.mkdtemp(prefix="stockflow_accp_"))
                    with zipfile.ZipFile(io.BytesIO(bytes(row.input_pack))) as z:
                        for m_ in z.infolist():
                            p_ = Path(m_.filename)
                            if not (m_.is_dir() or p_.is_absolute() or ".." in p_.parts):
                                (tmpa / p_.name).write_bytes(z.read(m_))
                    import daily
                    daily._unzip(tmpa)
                    rep = accuracy.for_inputs(tmpa, cfg)
                    shutil.rmtree(tmpa, ignore_errors=True)
                else:
                    st.warning("This plan's input files are no longer stored.")
            if rep:
                accuracy.save(PLAN_ID, rep, USER)
                log("accuracy.run", PLAN_ID, pairs=rep["pairs"])
        except ValueError as e_:
            st.warning(str(e_))
    if rep:
        lv = rep["levels"]
        sk, sc = lv["Store × SKU"], lv["Store × category"]
        kpis([("Error, store × item", f"{sk['stockflow_wape']:.0%}", f"simple average: {sk['simple_wape']:.0%}", TEAL),
              ("Error, store × category", f"{sc['stockflow_wape']:.0%}", f"simple average: {sc['simple_wape']:.0%}", TEAL),
              ("Bias", f"{sk['stockflow_bias']:+.0%}", "negative = forecast too low", SAFFRON if abs(sk["stockflow_bias"]) > .15 else GREEN),
              ("Compared", f"{rep['pairs']:,}", f"store × item pairs, {rep['cutoff']} to {rep['end']}")])
        bc = rep["by_category"]
        if len(bc):
            st.dataframe(bc, hide_index=True, width="stretch", column_config={
                "stockflow_wape": st.column_config.NumberColumn("StockFlow error", format="percent"),
                "simple_wape": st.column_config.NumberColumn("simple average error", format="percent"),
                "actual": st.column_config.NumberColumn("units sold", format="%d"), "forecast": st.column_config.NumberColumn(format="%d")})
        if abs(sk["stockflow_bias"]) > .15:
            note("The forecast was clearly too " + ("low" if sk["stockflow_bias"] < 0 else "high") + " in this window. That usually means a "
                 "season change the history does not cover yet (a year of sales and a calendar of events fix most of it).")


# ---- how it works
with T[10]:
    if not GUEST:
        accuracy_panel()
    (st.success if allp else st.error)(f"All {len(val)} rules passed on this plan." if allp else "Some rules failed. Don't send this plan.")
    st.markdown("""<div class="grid">
<div class="card"><div class="ic">📏</div><h4>Real selling speed</h4><p>Sales divided by days the size was actually on the shelf, blended with similar stores for new stores and launches.</p></div>
<div class="card"><div class="ic">🎉</div><h4>Your season</h4><p>Peak days and event weeks by region, from your calendar, lift or lower demand.</p></div>
<div class="card"><div class="ic">🧩</div><h4>Size sets</h4><p>Core sizes are the few that make most of a category's sales. Gaps are filled first; givers keep their last core size.</p></div>
<div class="card"><div class="ic">↩️</div><h4>Gentle returns</h4><p>Old stock comes back in capped steps per store. Season-end stock goes to an outlet or the warehouse.</p></div>
<div class="card"><div class="ic">💰</div><h4>Priced moves</h4><p>Every line is valued; lines and trips that cost more to move than they earn are dropped.</p></div>
<div class="card"><div class="ic">✅</div><h4>Checked twice</h4><p>Every business rule is re-checked on the final plan before you see it.</p></div></div>""", unsafe_allow_html=True)
    with st.expander(f"The {len(val)} rule checks"):
        for r_ in val.itertuples():
            st.markdown(f"{'✅' if r_.status == 'PASS' else '❌'} {r_.rule} <span style='color:{MUTED}'>(checked {r_.checked:,})</span>",
                        unsafe_allow_html=True)
    if pl.season.on and pl.season.lift:
        lift = pd.DataFrame(pl.season.lift).T.reset_index(names="region").melt("region", var_name="day", value_name="vs normal")
        plot(px.bar(lift, x="day", y="vs normal", color="region", barmode="group", title="Sales on special days (1.0 = a normal day)",
                    color_discrete_sequence=[TEAL, SAFFRON, PLUM, "#3E4C59", GREEN]).add_hline(y=1, line_dash="dot"), 320)

# ---- inventory dashboard (chain-wide)
with TABS["Inventory"]:
    it_ = pl.it
    inv = g.reset_index()
    inv = inv.assign(zone=inv.store_id.map(stores.zone), tier=inv.store_id.map(stores.tier), city=inv.store_id.map(stores.city),
                     cost=inv.sku_id.map(it_.cost), value=inv.oh * inv.mrp)
    if SCOPE:
        inv = inv[inv.store_id.isin(SCOPE)]
    a, b, c = st.columns(3)
    fz = a.multiselect("Zone", sorted(stores.zone.unique()), key="inv_z", placeholder="All zones")
    ft = b.multiselect("Tier", sorted(stores.tier.unique()), key="inv_t", placeholder="All tiers")
    fc = c.multiselect("Category", sorted(inv.dcat.unique()), key="inv_c", placeholder="All categories")
    if fz:
        inv = inv[inv.zone.isin(fz)]
    if ft:
        inv = inv[inv.tier.isin(ft)]
    if fc:
        inv = inv[inv.dcat.isin(fc)]
    snap_all = P["snap"]
    wh_stock = snap_all[snap_all.location_id.isin(pl.whs)].copy()
    wh_stock["value"] = wh_stock.oh * wh_stock.sku_id.map(it_.mrp)
    if fc:
        wh_stock = wh_stock[wh_stock.sku_id.map(it_.disp_cat).isin(fc)]
    selling = inv.fd30 >= 0.03
    aged = inv.age >= cfg["ageing"]["age_days"]
    st_pcs, wh_pcs = int(inv.oh.sum()), int(wh_stock.oh.sum())
    sales_day = float(inv.fd30.sum())
    kpis([("Pieces in the chain", f"{st_pcs + wh_pcs:,}", f"{st_pcs:,} in stores · {wh_pcs:,} in warehouses"),
          ("Value at MRP", M(float(inv.value.sum() + wh_stock.value.sum())), f"at cost {M(float((inv.oh * inv.cost).sum()))} in stores", PLUM),
          ("Store cover", f"{st_pcs / sales_day:.0f} days" if sales_day > 0 else "—", f"selling {sales_day:,.0f} pieces a day", TEAL),
          ("Sizes on the shelf", f"{(inv[selling].pos > 0).mean():.0%}" if selling.any() else "—", "of sizes that sell", GREEN),
          ("Old stock", f"{inv[aged].oh.sum() / max(st_pcs, 1):.0%}", f"of store pieces older than {cfg['ageing']['age_days']} days", RED),
          ("In transit", f"{int(inv.transit.sum()):,}", "pieces on the way to stores", SAFFRON)])
    sec("Where the stock is")
    loc = pd.concat([pd.DataFrame({"location": ["All stores"], "pieces": [st_pcs]}),
                     wh_stock.groupby("location_id").oh.sum().rename("pieces").reset_index().rename(columns={"location_id": "location"})])
    plot(px.bar(loc, x="pieces", y="location", orientation="h", color_discrete_sequence=[TEAL], title="Pieces by location",
                labels={"location": ""}), 80 + 40 * len(loc), False)
    sec("By category")
    cat = inv.groupby("dcat").agg(pieces=("oh", "sum"), value=("value", "sum"), sells_day=("fd30", "sum"))
    cat["cover_days"] = (cat.pieces / cat.sells_day).where(cat.sells_day > 0).round(0)
    cat["sizes_on_shelf"] = inv[selling].groupby("dcat").pos.apply(lambda x: (x > 0).mean())
    cat["old_share"] = inv[aged].groupby("dcat").oh.sum() / cat.pieces
    cat["warehouse_pieces"] = wh_stock.groupby(wh_stock.sku_id.map(it_.disp_cat)).oh.sum()
    cat = cat.fillna(0).reset_index().rename(columns={"dcat": "category"})
    st.dataframe(cat.assign(value=cat.value.map(M)), hide_index=True, width="stretch",
                 column_config={"sells_day": st.column_config.NumberColumn("sells/day", format="%.1f"),
                                "sizes_on_shelf": st.column_config.ProgressColumn("sizes on shelf", format="percent", min_value=0, max_value=1),
                                "old_share": st.column_config.ProgressColumn("old stock", format="percent", min_value=0, max_value=1)})
    sec("Store comparison")
    hs_ = E.hidden_stockouts(pl, R["weekly"], P["today"]).groupby("store_id").size()
    inn_ = mv[mv.to_id.isin(stores.index)].groupby("to_id").qty.sum()
    out_ = mv[mv.from_id.isin(stores.index)].groupby("from_id").qty.sum()
    cmpf = inv.groupby("store_id").agg(city=("city", "first"), zone=("zone", "first"), tier=("tier", "first"), pieces=("oh", "sum"),
                                       value=("value", "sum"), sells_day=("fd30", "sum"))
    cmpf["capacity_used"] = (cmpf.pieces / stores.cap.reindex(cmpf.index).clip(upper=10 ** 8)).where(stores.cap.reindex(cmpf.index) < 10 ** 8)
    cmpf["cover_days"] = (cmpf.pieces / cmpf.sells_day).where(cmpf.sells_day > 0).round(0)
    cmpf["sizes_on_shelf"] = inv[selling].groupby("store_id").pos.apply(lambda x: (x > 0).mean())
    cmpf["old_share"] = (inv[aged].groupby("store_id").oh.sum() / cmpf.pieces).fillna(0)
    cmpf["hidden_stockouts"] = hs_.reindex(cmpf.index).fillna(0).astype(int)
    cmpf["plan_in"] = inn_.reindex(cmpf.index).fillna(0).astype(int)
    cmpf["plan_out"] = out_.reindex(cmpf.index).fillna(0).astype(int)
    cmpf = cmpf.reset_index().rename(columns={"store_id": "store"})
    st.dataframe(cmpf, hide_index=True, width="stretch", height=min(520, 40 + 35 * len(cmpf)),
                 column_config={"value": st.column_config.NumberColumn("value (MRP)", format="%d"),
                                "sells_day": st.column_config.NumberColumn("sells/day", format="%.1f"),
                                "capacity_used": st.column_config.ProgressColumn("shelf used", format="percent", min_value=0, max_value=1.2),
                                "sizes_on_shelf": st.column_config.ProgressColumn("sizes on shelf", format="percent", min_value=0, max_value=1),
                                "old_share": st.column_config.ProgressColumn("old stock", format="percent", min_value=0, max_value=1)})
    if len(cmpf):
        med = float(cmpf.cover_days.median())
        fig = px.bar(cmpf.sort_values("cover_days"), x="store", y="cover_days", color="tier", title=f"Days of cover by store (median {med:.0f})",
                     labels={"cover_days": "days", "store": ""}, color_discrete_sequence=[TEAL, SAFFRON, PLUM, "#3E4C59", GREEN])
        fig.add_hline(y=med, line_dash="dot")
        plot(fig, 340)
        top = cmpf.sort_values("sells_day", ascending=False).head(40).store
        hm = inv[inv.store_id.isin(top)].groupby(["store_id", "dcat"]).agg(p=("oh", "sum"), f=("fd30", "sum"))
        hm = (hm.p / hm.f).where(hm.f > 0).unstack().reindex(top).clip(upper=180)
        if hm.notna().any().any():
            fig = px.imshow(hm.T, aspect="auto", color_continuous_scale=["#C2413A", "#F6E7C8", "#1E5C61"], zmin=0, zmax=120,
                            labels=dict(color="days", x="", y=""), title="Cover by store and category (red = running out, dark = overstocked)")
            plot(fig, 120 + 28 * hm.shape[1], False)
        st.download_button("⬇️ Store comparison (CSV)", safety.csv(cmpf), "store_comparison.csv", width="stretch")

# ---- audit trail
if "Audit" in TABS:
    with TABS["Audit"]:
        sec("Who did what, and when")
        st.caption("Every sign-in, plan, line decision, release, dispatch, receipt, settings change and alert is recorded. "
                   "Entries cannot be edited or deleted from the app.")
        ad = audit.as_frame(20000)
        if ad.empty:
            st.info("Nothing recorded yet.")
        else:
            ad["at"] = pd.to_datetime(ad["at"])
            a, b, c = st.columns(3)
            fu = a.multiselect("User", sorted(ad.user.dropna().unique()), key="au_u", placeholder="All users")
            fa = b.multiselect("Action", sorted(ad.what.unique()), key="au_a", placeholder="All actions")
            fd = c.date_input("From", value=ad["at"].min().date(), key="au_d")
            view = ad[ad["at"].dt.date >= fd]
            if fu:
                view = view[view.user.isin(fu)]
            if fa:
                view = view[view.what.isin(fa)]
            kpis([("Entries", f"{len(view):,}", f"of {len(ad):,}"), ("Users", view.user.nunique(), "active in this view"),
                  ("Line decisions", int(view.action.str.startswith("decision").sum()), "approve / reject / quantity", PLUM),
                  ("Failed sign-ins", int(view.action.isin(["login.failed", "login.locked"]).sum()), "watch for lockouts", RED)])
            st.dataframe(view.drop(columns="action").assign(at=view["at"].dt.strftime("%Y-%m-%d %H:%M:%S")), hide_index=True,
                         width="stretch", height=480, column_config={"details": st.column_config.TextColumn(width="large")})
            st.download_button("⬇️ Audit trail (CSV)", safety.csv(view), "audit_trail.csv", width="stretch")

# ---- admin: users, database, secrets
if "Admin" in TABS:
    with TABS["Admin"]:
        name, is_pg = db.backend()
        sec("System")
        kpis([("Database", name, db.describe(), GREEN if is_pg else RED),
              ("Plans saved", f"{len(plans.recent(10 ** 6)):,}", "can be reopened after a restart"),
              ("Sign-in", "Required", f"lock after {auth.LOCK_AFTER} wrong tries · sign-out after {auth.IDLE_MINUTES} min idle")])
        if not is_pg:
            note("⚠️ No PostgreSQL URL is set, so data is in a local SQLite file. On Streamlit Community Cloud that file is wiped on every "
                 "restart. Add <code>[database] url = \"postgresql://…\"</code> in the app's Settings → Secrets.")

        def _has(path_):
            try:
                cur = SECRETS
                for k_ in path_:
                    cur = cur[k_]
                return bool(cur)
            except Exception:  # noqa: BLE001
                return False
        sec_rows = [("Database URL", _has(["database", "url"]) or _has(["DATABASE_URL"])), ("Users in secrets", bool(USERS)),
                    ("WhatsApp Business API", bool(notify.configured(SECRETS))), ("Anthropic API key", _has(["ANTHROPIC_API_KEY"]))]
        st.markdown("**Secrets** (values are never shown): " + " · ".join(f"{'✅' if v else '—'} {k}" for k, v in sec_rows))
        sec("Your data")
        st.caption("Download everything StockFlow holds (one CSV per table plus your uploaded files) at any time, or delete it "
                   "all when you stop using StockFlow. Password and key hashes are never exported.")
        dx1_, dx2_ = st.columns(2)
        if dx1_.button("📦 Prepare full export", key="dx_prep", width="stretch"):
            st.session_state.dx_zip = db.export_all()
            log("data.export", "all", bytes=len(st.session_state.dx_zip))
        if st.session_state.get("dx_zip"):
            dx1_.download_button("⬇️ Download export (.zip)", st.session_state.dx_zip, f"stockflow_export_{date.today():%Y%m%d}.zip",
                                 key="dx_dl", width="stretch")
        with dx2_.expander("🗑️ Delete all data"):
            st.warning("This deletes every plan, transfer, count, setting, log and draft. It cannot be undone. Download the export first.")
            keep_ = st.checkbox("Keep the user accounts", value=True, key="dx_keep")
            phrase_ = st.text_input("Type DELETE ALL DATA", key="dx_phrase")
            pw_ = st.text_input("Your password", type="password", key="dx_pw")
            if st.button("Delete everything", type="primary", key="dx_go", disabled=phrase_ != "DELETE ALL DATA" or not pw_):
                ok_, _r, _m = auth.attempt(USER, pw_, SECRETS)
                if not ok_:
                    st.error("Wrong password.")
                else:
                    n_ = db.purge_all(USER, keep_users=keep_)
                    for k_ in ("R", "run_id", "dx_zip"):
                        st.session_state.pop(k_, None)
                    st.success(f"Deleted {sum(n_.values()):,} records from {len([v for v in n_.values() if v])} tables.")
        sec("Users")
        everyone = auth.all_users(SECRETS)
        try:
            dbu = db.read("users")
        except Exception:  # noqa: BLE001
            dbu = pd.DataFrame()
        ul = pd.DataFrame([{"user": k, "role": v.get("role"), "stores": v.get("stores", ""), "source": v.get("source"),
                            "active": v.get("active", True)} for k, v in everyone.items()])
        if len(dbu):
            ul = ul.merge(dbu[["username", "last_login", "created_by"]].rename(columns={"username": "user"}), on="user", how="left")
        st.dataframe(ul, hide_index=True, width="stretch")
        with st.expander("👥 Add many users from a CSV"):
            st.caption("Columns: username, role (viewer / store / approver / admin), stores (optional, comma or ; separated). With company "
                       "sign-in set up, use work emails as user names and no passwords are made. Otherwise each new user gets a "
                       "temporary password, shown once below, which they must change at first sign-in.")
            upu = st.file_uploader("Users CSV", type=["csv"], key="bulk_users")
            if upu is not None and st.button("Create these users", key="bulk_go"):
                bu = pd.read_csv(upu, dtype=str, keep_default_na=False)
                bu.columns = [c.strip().lower() for c in bu.columns]
                made, errs = [], []
                import secrets as _sec
                for r_ in bu.itertuples():
                    un = str(getattr(r_, "username", "")).strip()
                    rl = str(getattr(r_, "role", "viewer")).strip().lower() or "viewer"
                    stx = str(getattr(r_, "stores", "")).replace(";", ",")
                    if rl not in ("viewer", "store", "approver", "admin"):
                        errs.append(f"{un}: unknown role {rl}")
                        continue
                    sso_only = bool(sso_providers()) and "@" in un
                    pw = _sec.token_urlsafe(24) + "9a" if sso_only else _sec.token_urlsafe(9) + "7"
                    try:
                        auth.create_user(un, pw, rl, stx, USER, must_change=True)
                        made.append(dict(username=un.lower() if "@" in un else un, role=rl, stores=stx,
                                         temporary_password="(company sign-in)" if sso_only else pw))
                    except ValueError as e_:
                        errs.append(f"{un}: {e_}")
                log("user.bulk", f"{len(made)} users", created=len(made), errors=len(errs))
                if made:
                    st.success(f"Created {len(made)} users. Download the list now and share each password privately; it is not shown again.")
                    st.download_button("⬇️ New users and temporary passwords", safety.csv(pd.DataFrame(made)), "new_users.csv")
                for e_ in errs[:20]:
                    st.warning(e_)
        st.caption("viewer: sees plans · store: + marks dispatch/receipt for its stores · approver: + line decisions, release, all tracking, "
                   "audit · admin: + settings and users. Users in secrets can't be changed here.")
        a, b = st.columns(2, gap="large")
        with a, st.form("new_user", clear_on_submit=True):
            st.markdown("**Add a user**")
            nu = st.text_input("User name")
            nr = st.selectbox("Role", ["viewer", "store", "approver", "admin"])
            ns = st.multiselect("Limit to stores (optional)", list(stores.index))
            npw = st.text_input("Temporary password (they change it at first sign-in)" +
                                (" — leave blank for company sign-in (user name = work email)" if sso_providers() else ""), type="password")
            if st.form_submit_button("Create user", type="primary", width="stretch"):
                try:
                    if not npw and sso_providers() and "@" in nu:
                        import secrets as _sec
                        npw = _sec.token_urlsafe(24) + "9a"           # unusable password: this user signs in through SSO only
                    auth.create_user(nu, npw, nr, ", ".join(ns), USER, must_change=True)
                    log("user.create", nu.strip(), role=nr, stores=", ".join(ns))
                    st.success(f"Created {nu.strip()}.")
                except ValueError as e:
                    st.error(str(e))
        with b:
            dbnames = list(dbu.username) if len(dbu) else []
            if dbnames:
                with st.form("edit_user"):
                    st.markdown("**Change a user**")
                    eu = st.selectbox("User", dbnames)
                    cur = dbu.set_index("username").loc[eu]
                    er = st.selectbox("Role", ["viewer", "store", "approver", "admin"],
                                      index=["viewer", "store", "approver", "admin"].index(cur.role) if cur.role in auth.PERMS else 0)
                    es = st.text_input("Stores (comma separated, blank = all)", cur.stores or "")
                    ea = st.checkbox("Active", value=bool(cur.active))
                    epw = st.text_input("New temporary password (optional)", type="password")
                    if st.form_submit_button("Save changes", width="stretch"):
                        if eu == USER and (not ea or er != "admin"):
                            st.error("You can't remove your own admin access.")
                        else:
                            vals = dict(role=er, stores=es, active=ea)
                            probs = auth.password_problems(epw, eu) if epw else []
                            if probs:
                                st.error("Password needs " + ", ".join(probs))
                            else:
                                if epw:
                                    vals.update(password=epw, must_change=True, failed=0, locked_until=None)
                                auth.update_user(eu, **vals)
                                log("user.update", eu, role=er, stores=es, active=ea, password_reset=bool(epw))
                                st.success("Saved.")


# ============================================================================= operations layer
def trip_region(fr, to):
    z = stores.zone.to_dict()
    return z.get(to) or z.get(fr) or "Warehouse"


def queue_frame():
    """Today's trips with region, decision (regional queue) and release state."""
    q = tr.copy()
    q["region"] = [trip_region(a, b) for a, b in zip(q.from_id, q.to_id)]
    q["priority"] = (q.urgent > 0).map({True: "Urgent", False: "Routine"}) if "urgent" in q else "Routine"
    try:
        d = db.read("trip_decisions", {"plan_id": PLAN_ID}) if PLAN_ID else pd.DataFrame()
    except Exception:  # noqa: BLE001
        d = pd.DataFrame()
    dd = d.set_index("to_number") if len(d) else pd.DataFrame(columns=["decision", "by", "at", "note"])
    q["decision"] = q.to_number.map(dd.decision).fillna("pending") if len(dd) else "pending"
    q["decided_by"] = q.to_number.map(dd["by"]).fillna("") if len(dd) else ""
    q["queue_note"] = q.to_number.map(dd.note).fillna("") if len(dd) else ""
    rel = fb.released()
    done = set(rel[rel.run_id == PLAN_ID].to_number) if len(rel) and "run_id" in rel else set()
    q["released"] = q.to_number.isin(done)
    q["state"] = q.released.map({True: "released", False: ""}).where(q.released, q.decision)
    return q


def release_trips(numbers, where):
    """Release the given trips (line decisions applied), send them to the ERP/WMS if configured. Returns a message."""
    o = R["orders"]
    o = o[o.to_number.isin(numbers)] if len(o) else o
    if PLAN_ID:
        o = plans.apply_decisions(o, PLAN_ID)
    if o is None or o.empty:
        return False, "Nothing to release."
    skipped = fb.release_orders(o, USER, PLAN_ID)
    o = o[~o.to_number.isin(skipped)]
    if o.empty:
        return False, "These transfer orders were already released."
    log("orders.release", PLAN_ID, orders=int(o.to_number.nunique()), lines=len(o), pieces=int(o.qty.sum()), via=where)
    msg = f"Released {o.to_number.nunique()} transfer orders ({int(o.qty.sum()):,} pieces)." + \
        (f" {len(skipped)} were already released and were skipped." if skipped else "")
    if erp_on():
        ok, m = erp.export_orders(o, PLAN_ID, USER, SECRETS)
        log("erp.export", PLAN_ID, ok=ok, orders=int(o.to_number.nunique()), message=m[:200])
        msg += " " + m
    st.session_state.released = o.copy()
    import webhooks
    webhooks.emit("trips.released", {"plan_id": PLAN_ID, "to_numbers": sorted(o.to_number.unique()), "pieces": int(o.qty.sum())})
    return True, msg


if "Operations" in TABS:
    with TABS["Operations"]:
        try:
            late_ = XQ.check_deadlines(PLAN_ID, user=USER) if PLAN_ID else []
        except Exception:  # noqa: BLE001
            late_ = []
        if late_:
            note(f"⏰ <b>Approvals overdue</b> after the {html.escape(str(cfg.get('operations', {}).get('approve_by', '11:00')))} cut-off in: "
                 f"{html.escape(', '.join(late_))}.")
        q = queue_frame()
        regions = sorted(q.region.unique())
        mine = sorted({trip_region(s_, s_) for s_ in SCOPE}) if SCOPE else regions
        xs = XQ.summary()
        jb = jobs.recent(30)
        kpis([("Waiting for a decision", f"{int((q.state == 'pending').sum())} trips", f"{int(q[q.state == 'pending'].pieces.sum()):,} pieces"),
              ("Approved, not released", f"{int((q.state == 'approved').sum())}", "ready to release", TEAL),
              ("Released today", f"{int(q.released.sum())} of {len(q)}", "transfer orders out", GREEN),
              ("Open exceptions", f"{xs['open']}", f"{xs['high']} high · oldest {xs['oldest_days']} days", RED if xs["high"] else SAFFRON)])
        sec("Regional approval queues")
        st.caption("Each trip sits in the queue of the region it serves (the receiving store's zone; returns to a warehouse go to the "
                   "sender's zone). Approvers limited to some stores see their regions only. A trip rejected here is left out of the "
                   "transfer orders everywhere, including the Approve tab.")
        summ = q.groupby("region").agg(trips=("to_number", "size"), pending=("state", lambda x: int((x == "pending").sum())),
                                       approved=("state", lambda x: int((x == "approved").sum())),
                                       rejected=("state", lambda x: int((x == "rejected").sum())), released=("released", "sum"),
                                       pieces=("pieces", "sum"), net=("net_benefit", "sum"),
                                       urgent_pending=("priority", lambda x: int(((x == "Urgent") & (q.loc[x.index, "state"] == "pending")).sum())))
        st.dataframe(summ.reset_index().assign(net=lambda d: d.net.map(M)), hide_index=True, width="stretch")
        pick = st.pills("Queue", [r_ for r_ in regions if r_ in mine], selection_mode="single", default=(mine[0] if mine else None), key="q_reg")
        if pick:
            qq = q[q.region == pick].sort_values(["released", "priority", "net_benefit"], ascending=[True, False, False])
            can_q = can_do("approve") and (not SCOPE or bool(set(qq.from_id) | set(qq.to_id)) & bool(SCOPE))
            ed_in = pd.DataFrame({"trip": qq.to_number, "route": qq.from_id + " → " + qq.to_id, "reason": qq.reason,
                                  "pieces": qq.pieces, "net": qq.net_benefit.round(0), "priority": qq.priority,
                                  "decision": qq.decision, "note": qq.queue_note, "by": qq.decided_by, "released": qq.released})
            ed = st.data_editor(ed_in, hide_index=True, width="stretch", key=f"q_ed_{PLAN_ID}_{pick}",
                                disabled=["trip", "route", "reason", "pieces", "net", "priority", "by", "released"] if can_q else True,
                                column_config={"decision": st.column_config.SelectboxColumn(options=["pending", "approved", "rejected"]),
                                               "reason": st.column_config.TextColumn(width="large"), "net": st.column_config.NumberColumn(format="%d")})
            if can_q:
                a, b, c = st.columns(3)
                if a.button("💾 Save queue decisions", type="primary", width="stretch", key="q_save"):
                    n = 0
                    for r_ in ed.itertuples():
                        old = qq[qq.to_number == r_.trip].iloc[0]
                        if r_.released or (r_.decision == old.decision and (r_.note or "") == (old.queue_note or "")):
                            continue
                        if SCOPE and not ({old.from_id, old.to_id} & set(SCOPE)):
                            continue
                        db.delete_rows("trip_decisions", {"plan_id": PLAN_ID, "to_number": r_.trip})
                        db.insert_rows("trip_decisions", [dict(plan_id=PLAN_ID, to_number=r_.trip, region=pick, decision=r_.decision,
                                                               by=USER, at=datetime.now(), note=r_.note or "")])
                        log("queue.decide", f"{PLAN_ID}/{r_.trip}", region=pick, before=old.decision, after=r_.decision, note=r_.note or "")
                        n += 1
                    st.success(f"Saved {n} decisions.")
                    st.rerun()
                if b.button("Approve all pending in this queue", width="stretch", key="q_all"):
                    for r_ in qq[(qq.state == "pending")].itertuples():
                        db.delete_rows("trip_decisions", {"plan_id": PLAN_ID, "to_number": r_.to_number})
                        db.insert_rows("trip_decisions", [dict(plan_id=PLAN_ID, to_number=r_.to_number, region=pick, decision="approved",
                                                               by=USER, at=datetime.now(), note="bulk")])
                    log("queue.decide", f"{PLAN_ID}/{pick}", region=pick, after="approved", trips=int((qq.state == "pending").sum()), bulk=True)
                    st.rerun()
                ready = qq[(qq.decision == "approved") & ~qq.released]
                if c.button(f"📤 Release {len(ready)} approved trips", width="stretch", key="q_rel", disabled=ready.empty or not can_do("release")):
                    ok, m = release_trips(set(ready.to_number), f"queue:{pick}")
                    (st.success if ok else st.warning)(m)
            else:
                st.caption("You can see this queue but not decide on it.")

        sec("Transfer status by region")
        tk, _ = fb.tracking()
        if tk is None or len(tk) == 0:
            st.info("No transfers released yet.")
        else:
            locs = XQ.locations()
            tk["region"] = [(locs.get(t_) or {}).get("zone") or (locs.get(f_) or {}).get("zone") or "—" for f_, t_ in zip(tk.from_location, tk.to_location)]
            if SCOPE:
                tk = tk[tk.from_location.isin(SCOPE) | tk.to_location.isin(SCOPE)]
            pv = tk.pivot_table(index="region", columns="status", values="to_number", aggfunc="count", fill_value=0)
            pv = pv[[c_ for c_ in fb.STATUS_ORDER if c_ in pv.columns]]
            st.dataframe(pv, width="stretch")
            late = tk[tk.status.isin(["Late leaving", "Late arriving", "Received short", "Received over"])]
            if len(late):
                st.markdown(f"**Needs attention ({len(late)})**")
                st.dataframe(late[["to_number", "region", "from_location", "to_location", "status", "planned", "sent", "received", "ship_date",
                                   "expected"]], hide_index=True, width="stretch")
        sec("ERP / WMS exchange")
        ec = erp.conf(SECRETS)
        st.caption(("Connected: " if erp_on() else "Not configured in secrets; using the local folder ") + erp.describe(ec) +
                   ". Released orders are sent automatically; dispatch and receipt confirmations are picked up below and by the daily run.")
        a, b = st.columns(2)
        if a.button("📥 Pick up confirmations now", width="stretch", key="erp_poll", disabled=not can_do("track_all")):
            r_ = erp.poll(USER, SECRETS)
            log("erp.poll", "inbox", **{k: v for k, v in r_.items() if k != "errors"})
            XQ.sync_transfers(USER)
            (st.success if r_.get("ok") else st.error)(f"{r_.get('files', 0)} files · {r_.get('dispatched', 0)} dispatch and "
                                                        f"{r_.get('received', 0)} receipt lines" + (f" · {r_['error']}" if r_.get("error") else ""))
        rel = fb.released()
        sent = erp.log(500)
        sent_ok = set(sent[(sent.direction == "out") & (sent.status == "ok")].file) if len(sent) else set()
        if b.button("🔁 Re-send today's released orders", width="stretch", key="erp_resend", disabled=not can_do("release") or rel.empty):
            o_ = rel[rel.run_id == PLAN_ID] if "run_id" in rel else rel.iloc[0:0]
            ok, m = erp.export_orders(o_, PLAN_ID, USER, SECRETS)
            log("erp.export", PLAN_ID, ok=ok, resend=True, message=m[:200])
            (st.success if ok else st.error)(m)
        if len(sent):
            with st.expander(f"Exchange log ({len(sent)})"):
                st.dataframe(sent.drop(columns=["checksum"], errors="ignore"), hide_index=True, width="stretch")
        sec("Automation and background jobs")
        auto = jb[jb.kind == "daily"] if len(jb) else jb
        last = auto.iloc[0] if len(auto) else None
        st.markdown("**Daily run:** " + (f"last {pd.Timestamp(last.requested_at):%d %b %H:%M} — {last.status}"
                                           + (f" ({html.escape(str(last.error)[:120])})" if last.status == "failed" else "") if last is not None
                                           else "has not run yet") + f" · scheduled {html.escape(str((db.get_setting('automation', {}) or {}).get('run_at', '06:30')))} daily")
        if can_do("settings") and st.button("▶️ Run the daily plan now", key="daily_now"):
            import daily
            sec_ = SECRETS
            jid = jobs.submit("daily", lambda p_: daily.run_daily(USER, None, False, True, p_, dict(sec_) if sec_ is not None else {}),
                              USER, label="Daily run (manual)")
            log("automation.run", jid, manual=True)
            st.success("Started. Progress shows below; the new plan appears in 'Pick up where you left off'.")
        if len(jb):
            st.dataframe(jb[["requested_at", "kind", "status", "step", "progress", "requested_by", "plan_id", "error"]], hide_index=True,
                         width="stretch", column_config={"progress": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})

if "Exceptions" in TABS:
    with TABS["Exceptions"]:
        sec("Exceptions")
        st.caption("Data-quality problems from the files, stock-data problems the planner found, and transfers that are late or arrived "
                   "short. Each stays open until resolved; problems that disappear from the data are closed automatically.")
        if st.button("🔄 Re-check transfers", key="x_sync"):
            r_ = XQ.sync_transfers(USER)
            st.toast(f"{r_['new']} new · {r_['cleared']} cleared")
        show_all = st.toggle("Include resolved and cleared", False, key="x_all")
        xt = XQ.table(None if show_all else ("open", "assigned"))
        if SCOPE and len(xt):
            if ROLE == "store":
                xt = xt[xt.location.isin(SCOPE) | (xt.location == "")]
            else:
                locs_ = XQ.locations()
                zones_ = {(locs_.get(s_) or {}).get("zone") for s_ in SCOPE}
                xt = xt[xt.location.isin(SCOPE) | xt.region.isin(zones_) | (xt.location == "")]
        if xt.empty:
            st.success("No open exceptions.")
        else:
            a, b, c = st.columns(3)
            fk = a.multiselect("Type", sorted(xt.kind.unique()), format_func=lambda k: XQ.KINDS.get(k, k), key="x_k", placeholder="All types")
            fs = b.multiselect("Severity", ["high", "medium", "low"], key="x_s", placeholder="All")
            fr = c.multiselect("Region", sorted(x_ for x_ in xt.region.dropna().unique() if x_), key="x_r", placeholder="All regions")
            v_ = xt
            if fk:
                v_ = v_[v_.kind.isin(fk)]
            if fs:
                v_ = v_[v_.severity.isin(fs)]
            if fr:
                v_ = v_[v_.region.isin(fr)]
            age = (pd.Timestamp.now() - pd.to_datetime(v_.first_seen)).dt.days
            kpis([("Open", f"{int(v_.status.isin(['open', 'assigned']).sum())}", f"{len(v_)} shown"),
                  ("High", f"{int((v_.severity == 'high').sum())}", "fix before approving", RED),
                  ("Oldest", f"{int(age.max()) if len(age) else 0} days", "since first seen", SAFFRON),
                  ("Seen repeatedly", f"{int((v_.seen_count > 1).sum())}", "came back on later runs", PLUM)])
            ed_in = pd.DataFrame({"pick": False, "id": v_.id, "severity": v_.severity, "type": v_.kind.map(XQ.KINDS).fillna(v_.kind),
                                  "region": v_.region, "where": v_.location, "title": v_.title, "detail": v_.detail,
                                  "days_open": age.values, "seen": v_.seen_count, "status": v_.status, "owner": v_.owner,
                                  "resolution": v_.resolution})
            ed = st.data_editor(ed_in, hide_index=True, width="stretch", key="x_ed", disabled=[c_ for c_ in ed_in.columns if c_ != "pick"],
                                column_config={"id": None, "pick": st.column_config.CheckboxColumn("✓"),
                                               "detail": st.column_config.TextColumn(width="large")})
            chosen = list(ed[ed.pick].id)
            if can_do("resolve"):
                a, b = st.columns(2)
                with a.form("x_resolve", clear_on_submit=True):
                    nt = st.text_input("What was done", placeholder="e.g. GRN posted in ERP; stock corrected")
                    if st.form_submit_button(f"✅ Resolve {len(chosen)} selected", width="stretch", type="primary"):
                        if not chosen or not nt.strip():
                            st.warning("Tick the exceptions and say what was done.")
                        else:
                            XQ.resolve(chosen, USER, nt.strip())
                            log("exception.resolve", ",".join(map(str, chosen))[:200], count=len(chosen), note=nt.strip()[:200])
                            st.rerun()
                with b.form("x_assign", clear_on_submit=True):
                    who = st.selectbox("Assign to", sorted(auth.all_users(SECRETS)))
                    if st.form_submit_button(f"👤 Assign {len(chosen)} selected", width="stretch"):
                        if chosen:
                            XQ.assign(chosen, who)
                            log("exception.assign", ",".join(map(str, chosen))[:200], owner=who, count=len(chosen))
                            st.rerun()
            st.download_button("⬇️ Exceptions (CSV)", safety.csv(ed_in.drop(columns="pick")), "exceptions.csv", width="stretch")

if "Integrations" in TABS:
    with TABS["Integrations"]:
        sec("Column mapping profiles")
        st.caption("Upload one export from your POS, ERP or warehouse system. StockFlow recognises the sheet and suggests a column for each "
                   "field; correct any it got wrong and save. Every later file with the same headers, from uploads or the daily run, "
                   "is read the same way.")
        smp = st.file_uploader("Sample export (CSV or Excel)", type=["csv", "xlsx", "xls", "txt", "tsv"], key="map_up")
        profiles = ingest.load_profiles()
        if smp is not None:
            tmpd = Path(tempfile.mkdtemp(prefix="stockflow_map_"))
            (tmpd / smp.name).write_bytes(smp.getvalue())
            raw = {k_: v_ for k_, v_ in ingest.read_raw(tmpd).items() if not isinstance(v_, Exception)}
            shutil.rmtree(tmpd, ignore_errors=True)
            if not raw:
                st.error("That file could not be read.")
            else:
                nm, df_ = next(iter(raw.items()))
                df_ = ingest._header_row(df_)
                guess = ingest.detect_sheet(nm.split("#")[-1], df_.columns)
                sheets_ = list(schema.TABLES)
                sh = st.selectbox("This file is", sheets_, index=sheets_.index(guess) if guess in sheets_ else 0, key="map_sheet")
                sugg = ingest.suggest_mapping(sh, df_.columns, profiles.get(sh))
                inv = {v_: k_ for k_, v_ in sugg.items()}
                opts = ["— not in this file —"] + list(map(str, df_.columns))
                cols_ = st.columns(3)
                chosen_map = {}
                for i_, (c_, need_, meaning_, _) in enumerate(schema.TABLES[sh][2]):
                    cur_ = inv.get(c_)
                    chosen_map[c_] = cols_[i_ % 3].selectbox(f"{c_}{' *' if need_ == schema.R else ''}", opts,
                                                             index=opts.index(str(cur_)) if cur_ is not None and str(cur_) in opts else 0,
                                                             help=meaning_, key=f"map_{sh}_{c_}")
                mapping_ = {v_: k_ for k_, v_ in chosen_map.items() if v_ != opts[0]}
                clean_, notes_ = ingest.standardise(df_.head(2000), sh, mapping_)
                miss_ = [c_ for c_ in ingest.required(sh) if c_ not in clean_]
                (st.error if miss_ else st.success)(("Still missing required: " + ", ".join(miss_)) if miss_ else
                                                     f"All required columns found. {' · '.join(notes_)}")
                st.dataframe(clean_.head(20), hide_index=True, width="stretch")
                if st.button("💾 Save mapping profile", type="primary", key="map_save"):
                    ingest.save_profile(sh, {k_: v_ for k_, v_ in chosen_map.items() if v_ != opts[0]}, USER)
                    log("mapping.save", sh, columns=len(mapping_), file=smp.name[:80])
                    st.success(f"Saved. Files recognised as {sh} will use it.")
        if profiles:
            st.markdown("**Saved profiles:** " + " · ".join(f"{k_} ({len(v_)} columns)" for k_, v_ in profiles.items()))
        try:
            ig = db.read("ingestions", order=["-at"], limit=300)
        except Exception:  # noqa: BLE001
            ig = pd.DataFrame()
        if len(ig):
            with st.expander(f"Ingestion log ({len(ig)} files)"):
                st.dataframe(ig.drop(columns=["id", "mapping"], errors="ignore"), hide_index=True, width="stretch")
        sec("Daily automation")
        st.caption("The daily run fetches the day's exports, standardises them, builds the plan and saves it for approval. Nothing is "
                   "released automatically. Credentials (SFTP password, API token) go in secrets under [automation], never here.")
        au = dict(db.get_setting("automation", {}) or {})
        with st.form("auto_form"):
            a, b = st.columns(2)
            src_ = a.selectbox("Source", ["folder", "sftp", "url"], index=["folder", "sftp", "url"].index(au.get("source", "folder")))
            at_ = b.text_input("Run at (24h, local time)", au.get("run_at", "06:30"))
            path_ = st.text_input("Folder path (folder) or remote folder (SFTP)", au.get("path", "") or au.get("remote_dir", ""))
            urls_ = st.text_area("File URLs, one per line (url)", "\n".join(au.get("urls", []) if isinstance(au.get("urls"), list) else []))
            host_ = st.text_input("SFTP host (password or key in secrets)", au.get("host", ""))
            prof_ = st.selectbox("Settings profile", [""] + [b_.stem for b_ in BRANDS],
                                 index=([""] + [b_.stem for b_ in BRANDS]).index(au.get("brand_profile", "")) if au.get("brand_profile", "") in [b_.stem for b_ in BRANDS] else 0)
            if st.form_submit_button("Save automation settings", type="primary"):
                if not re.match(r"^\d{1,2}:\d{2}$", at_.strip()):
                    st.error("Use HH:MM for the time.")
                else:
                    new_ = dict(source=src_, run_at=at_.strip(), path=path_.strip() if src_ == "folder" else "",
                                remote_dir=path_.strip() if src_ == "sftp" else "", host=host_.strip(),
                                urls=[u_.strip() for u_ in urls_.splitlines() if u_.strip()], brand_profile=prof_)
                    db.upsert_setting("automation", new_, USER)
                    log("automation.settings", "automation", **{k_: (v_ if k_ != "urls" else len(v_)) for k_, v_ in new_.items()})
                    st.success("Saved.")
        st.markdown("Schedule it with the included GitHub Actions workflow (`.github/workflows/daily-plan.yml`), with cron "
                    "(`30 6 * * * cd stock_mover && python daily.py`), or Windows Task Scheduler (`run_daily.ps1`).")
        sec("API keys and webhooks")
        st.caption("API keys let another system (WMS, ERP, BI) use the StockFlow API with a role. The key is shown once; only a hash is "
                   "stored. API documentation: /api/docs on the API server.")
        with st.form("apikey", clear_on_submit=True):
            a, b, c = st.columns([2, 1, 2])
            kn = a.text_input("Key name (e.g. WMS robot)")
            kr = b.selectbox("Role", ["viewer", "store", "approver"])
            ks = c.text_input("Limit to locations (comma separated, optional)")
            if st.form_submit_button("Create API key") and kn.strip():
                import api as _api
                raw = _api.make_api_key(kn.strip(), kr, ks.strip(), USER)
                st.success("Copy this key now; it will not be shown again.")
                st.code(raw, language=None)
        try:
            ks_ = db.read("api_keys")
        except Exception:  # noqa: BLE001
            ks_ = pd.DataFrame()
        if len(ks_):
            st.dataframe(ks_.drop(columns=["hash"]), hide_index=True, width="stretch")
            kill = st.selectbox("Revoke a key", [""] + list(ks_[ks_.active.astype(bool)].key_id), key="kill_key")
            if kill and st.button(f"Revoke {kill}", key="kill_btn"):
                db.update_rows("api_keys", {"key_id": kill}, {"active": False})
                log("apikey.revoke", kill)
                st.rerun()
        import webhooks as _wh
        subs = _wh.subscriptions()
        wtxt = st.text_area("Webhooks: one per line as  URL | events (comma separated, blank = all) | signing secret",
                            "\n".join(f"{s_['url']} | {','.join(s_['events'])} | {'••••' if s_.get('secret') else ''}" for s_ in subs),
                            help="Events: " + ", ".join(_wh.EVENTS) + ". Payloads are signed: X-StockFlow-Signature = sha256 HMAC.")
        if st.button("Save webhooks", key="wh_save"):
            new_ = []
            for ln in [x for x in wtxt.splitlines() if x.strip()]:
                parts = [p_.strip() for p_ in ln.split("|")] + ["", ""]
                old_ = next((s_ for s_ in subs if s_["url"] == parts[0]), {})
                new_.append(dict(url=parts[0], events=[e_.strip() for e_ in parts[1].split(",") if e_.strip()],
                                 secret=old_.get("secret", "") if parts[2] == "••••" else parts[2]))
            try:
                _wh.save_subscriptions(new_, USER)
                log("webhooks.set", "webhooks", count=len(new_))
                st.success(f"Saved {len(new_)} webhooks.")
            except ValueError as e_:
                st.error(str(e_))
        wl = _wh.log(50)
        if wl is not None and len(wl):
            with st.expander(f"Webhook deliveries ({len(wl)})"):
                st.dataframe(wl, hide_index=True, width="stretch")
        sec("Reading stock from images")
        av = dict(db.get_setting("ai_vision", {}) or {})
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY")) or bool(secret("ANTHROPIC_API_KEY"))
        st.caption("Barcodes and printed count sheets are read on this server. Handwritten registers and difficult photos can be read "
                   "by AI (Claude); the image is then sent to Anthropic. Check your data-processing agreement covers this first.")
        st.markdown(f"**AI reading:** {'on' if av.get('enabled') else 'off'}" + ("" if has_key else " · ⚠️ no ANTHROPIC_API_KEY in secrets"))
        if av.get("enabled"):
            if st.button("Turn AI image reading off", key="aiv_off"):
                db.upsert_setting("ai_vision", dict(enabled=False), USER)
                log("ai.vision", "off")
                st.rerun()
        elif st.checkbox("Sending count-sheet images to the AI provider is covered by our data-processing agreement", key="aiv_ok") \
                and st.button("Turn AI image reading on", key="aiv_on", disabled=not has_key):
            db.upsert_setting("ai_vision", dict(enabled=True, by=USER, at=datetime.now().isoformat(timespec="seconds")), USER)
            log("ai.vision", "on")
            st.rerun()
        sec("AI agents")
        import agents as _ag
        ag_on, _ = _ag.ai_status(SECRETS)
        st.caption("The agents (Agents tab) always work from rules. With AI on, Claude writes their drafts and looks up facts with "
                   "read-only tools; plan facts (stock, sales, transfer and exception details, store names) are then sent to Anthropic. "
                   "Agents never act on their own: every draft waits for a person to approve it.")
        st.markdown(f"**AI agents:** {'on' if ag_on else 'off'}" + ("" if has_key else " · ⚠️ no ANTHROPIC_API_KEY in secrets"))
        if ag_on:
            if st.button("Turn AI agents off", key="aia_off"):
                _ag.set_ai(False, USER)
                st.rerun()
        elif st.checkbox("Sending plan data to the AI provider is covered by our data-processing agreement", key="aia_ok") \
                and st.button("Turn AI agents on", key="aia_on", disabled=not has_key):
            _ag.set_ai(True, USER)
            st.rerun()
        sec("AI spending cap")
        import ai_budget as _ab
        bs_ = _ab.summary()
        st.progress(min(1.0, bs_["spent_inr"] / bs_["cap_inr"]) if bs_["cap_inr"] else 1.0,
                    text=f"{bs_['month']}: ₹{bs_['spent_inr']:,.0f} of ₹{bs_['cap_inr']:,.0f} used · {bs_['calls']} AI calls"
                         + (f" · {bs_['blocked']} skipped at the cap" if bs_["blocked"] else ""))
        if bs_["by_feature"]:
            st.caption("By feature: " + ", ".join(f"{k} ₹{v:,.0f}" for k, v in sorted(bs_["by_feature"].items(), key=lambda x: -x[1])))
        st.caption("Covers every AI feature (agents, Ask, reading photos, growth messages). A call that could go over the cap is not "
                   "made; the feature uses its rules instead. Counted at list prices: also set a spend limit in the Claude Console.")
        cap_ = st.number_input("Monthly cap (₹)", 0, 100000, int(bs_["cap_inr"]), step=100, key="ai_cap")
        if cap_ != int(bs_["cap_inr"]) and st.button("Save cap", key="ai_cap_save"):
            _ab.set_cap(cap_, USER)
            log("ai.budget", "", cap_inr=cap_)
            st.rerun()
        sec("ERP / WMS connection")
        ec = erp.conf(SECRETS)
        st.markdown(f"**Channel:** {html.escape(erp.describe(ec))} · format **{ec.get('format')}** · "
                    + ("custom field names" if ec.get("fields") else "standard field names")
                    + ("" if erp_on() else " · ⚠️ not configured in secrets (local folder only)"))


# ---- money: what to buy (added after the existing Money content)
with T[1]:
    import buying
    sec("What to buy")
    st.caption("Stock no transfer can supply: each store's shortfall after today's trips, less spare warehouse stock and supplier "
               "deliveries already due, rounded up to pack size and minimum order. Hand it to buying as a starting point.")
    try:
        buy = buying.suggestions(R)
    except Exception as e_:  # noqa: BLE001
        buy = pd.DataFrame()
        st.caption(f"Not available for this plan ({type(e_).__name__}).")
    if SCOPE and len(buy):
        st.caption("Shown for the whole chain: buying is central.")
    if len(buy):
        kpis([("SKUs to buy", f"{len(buy):,}", f"{int(buy.suggest_buy.sum()):,} units"),
              ("At cost", M(float(buy.cost_value.sum())), "to cover the shortfall", PLUM),
              ("Stores short", f"{int(buy.stores_short.max())}", "most stores short of one SKU", SAFFRON)])
        if (buy.vendor != "").any():
            st.dataframe(buy.groupby("vendor", as_index=False).agg(skus=("sku_id", "size"), units=("suggest_buy", "sum"),
                                                                   cost=("cost_value", "sum")).assign(cost=lambda d: d.cost.map(M)),
                         hide_index=True, width="stretch")
        st.dataframe(buy.assign(cost_value=buy.cost_value.map(M)), hide_index=True, width="stretch", height=min(420, 40 + 35 * len(buy)),
                     column_config={"sells_per_day": st.column_config.NumberColumn("sells/day", format="%.1f")})
        st.download_button("⬇️ Purchase suggestions (CSV)", safety.csv(buy), f"purchase_suggestions_{pl.run:%Y-%m-%d}.csv", width="stretch")
    else:
        st.success("Nothing to buy: every shortfall can be covered by moving stock you already have.")


# ============================================================================= KPIs: today, predicted vs actual, history
def plan_std_inputs():
    """This plan's input files, standardised, in a temporary folder (caller deletes it). None if they are not stored."""
    import ingest
    row = plans.get(PLAN_ID) if PLAN_ID and not IN_BROWSER else None
    raw = Path(tempfile.mkdtemp(prefix="stockflow_kraw_"))
    std = Path(tempfile.mkdtemp(prefix="stockflow_kstd_"))
    try:
        if row is None or row.source == "demo":
            src = DEMO2 if "Demo Mart" in str(st.session_state.get("label", "")) else DEMO
        elif row.input_pack is not None:
            with zipfile.ZipFile(io.BytesIO(bytes(row.input_pack))) as z:
                for m_ in z.infolist():
                    p_ = Path(m_.filename)
                    if not (m_.is_dir() or p_.is_absolute() or ".." in p_.parts):
                        (raw / p_.name).write_bytes(z.read(m_))
            import daily
            daily._unzip(raw)
            for j in raw.glob("*.json"):
                j.unlink()
            src = raw
        else:
            shutil.rmtree(std, ignore_errors=True)
            return None
        ingest.run(src, std, cfg=cfg, log=False, stale=False)
        return std
    finally:
        shutil.rmtree(raw, ignore_errors=True)


def _arrow(now, after, fmt, good_up=True):
    if after is None or now is None or pd.isna(after) or pd.isna(now) or abs(after - now) < 1e-9:
        return ""
    better = (after > now) == good_up
    return f"{'▲' if after > now else '▼'} {fmt(after)} after today's plan" + (" ✓" if better else "")


with TABS["KPIs"]:
    import kpi
    K, KS = kpi.today(R)
    pct = lambda v: "—" if v is None or pd.isna(v) else f"{v:.0%}"  # noqa: E731
    days_ = lambda v: "—" if v is None or pd.isna(v) else f"{v:.0f} days"  # noqa: E731
    sec(f"Stock KPIs on {pd.Timestamp(K['as_of']):%d %b %Y}")
    kpis([("Stock on hand", f"{K['stock_units']:,} units", f"{M(K['stock_cost'])} at cost · {K['store_units']:,} in stores"),
          ("Days of cover (stores)", days_(K["cover_days"]), _arrow(K["cover_days"], K["cover_days_after"], days_), TEAL),
          ("In stock", pct(K["in_stock"]), _arrow(K["in_stock"], K["in_stock_after"], pct), GREEN),
          ("At risk of running out", f"{K['at_risk']:,}", _arrow(K["at_risk"], K["at_risk_after"], lambda v: f"{int(v):,}", good_up=False) or
           "store × item pairs before the next delivery", RED)])
    kpis([("Old stock", pct(K["old_share"]), "of store units", SAFFRON),
          ("Sell-through, 30 days", pct(K["sell_through"]), "sold ÷ (sold + on hand)", PLUM),
          ("Stock turns", "—" if pd.isna(K["turns"]) else f"{K['turns']:.1f}× a year", "cost of sales ÷ stock at cost"),
          ("Demand next 7 days", f"{K['pred7']:,.0f} units", f"if all on the shelf · sold last 7 days: {K['sold7']:,.0f}", TEAL)])
    with st.expander("What each KPI means"):
        st.markdown("\n".join(f"- **{n}**: {kpi.DEF[k]}" for n, k in (
            ("Stock on hand", "stock_units"), ("Days of cover", "cover_days"), ("In stock", "in_stock"), ("At risk", "at_risk"),
            ("Old stock", "old_share"), ("Sell-through", "sell_through"), ("Stock turns", "turns"), ("Demand next 7 days", "pred7"),
            ("Error", "wape"), ("Bias", "bias"))))
    ks = KS[KS.store_id.isin(SCOPE)] if SCOPE else KS
    st.dataframe(ks[["store_id", "units", "cover_days", "cover_days_after", "in_stock", "in_stock_after", "at_risk", "at_risk_after",
                     "old_share", "sold30", "pred7"]], hide_index=True, width="stretch",
                 column_config={"store_id": "store", "units": st.column_config.NumberColumn("units", format="%d"),
                                "cover_days": st.column_config.NumberColumn("cover days", format="%.0f"),
                                "cover_days_after": st.column_config.NumberColumn("after plan", format="%.0f"),
                                "in_stock": st.column_config.ProgressColumn("in stock", format="percent", min_value=0, max_value=1),
                                "in_stock_after": st.column_config.ProgressColumn("after plan", format="percent", min_value=0, max_value=1),
                                "at_risk": st.column_config.NumberColumn("at risk", format="%d"),
                                "at_risk_after": st.column_config.NumberColumn("after plan", format="%d"),
                                "old_share": st.column_config.ProgressColumn("old stock", format="percent", min_value=0, max_value=1),
                                "sold30": st.column_config.NumberColumn("sold 30 days", format="%d"),
                                "pred7": st.column_config.NumberColumn("demand next 7 days", format="%.0f")})

    sec("Predicted vs actual sales, week by week")
    st.caption("For each of the last 8 weeks, the forecast is rebuilt from the data as it stood at the start of that week and compared "
               "with what then sold, next to the simple 'average of the last 4 weeks'. Counted only where the item was on the shelf "
               "most of the week (or, without weekly stock records, where the store carries it), so empty shelves don't count as "
               "forecast errors.")
    import db as _db
    wrep = None
    cached = _db.get_setting(f"kpiw:{PLAN_ID}"[:40]) if PLAN_ID and not IN_BROWSER else None
    if cached:
        wrep = pd.DataFrame(cached)
    elif not GUEST and st.button("📈 Build the weekly comparison", key="kpiw_go"):
        bar = st.progress(0.0, text="Starting…")
        std_ = plan_std_inputs()
        try:
            if std_ is None:
                st.warning("This plan's input files are no longer stored, so past weeks can't be rebuilt.")
            else:
                wrep = kpi.replay(std_, cfg, 8, progress=lambda t_, p_: bar.progress(min(1.0, p_), text=t_))
                if PLAN_ID:
                    _db.upsert_setting(f"kpiw:{PLAN_ID}"[:40], json.loads(wrep.to_json(orient="records")), USER)
                log("kpi.replay", PLAN_ID or "demo", weeks=int(wrep.week_start.nunique()))
                if PLAN_ID:
                    st.rerun()                       # show the saved comparison without the build button
        except ValueError as e_:
            st.warning(str(e_))
        finally:
            bar.empty()
            if std_:
                shutil.rmtree(std_, ignore_errors=True)
    elif GUEST:
        st.caption("Sign in to build the weekly comparison.")
    if wrep is not None and len(wrep):
        a_, b_ = st.columns(2)
        fs_ = a_.multiselect("Stores", sorted(wrep.store_id.unique()), default=[s_ for s_ in SCOPE if s_ in set(wrep.store_id)] if SCOPE else [],
                            key="kpiw_s", placeholder="All stores")
        fc_ = b_.multiselect("Categories", sorted(wrep.category.unique()), key="kpiw_c", placeholder="All categories")
        sub = wrep
        if fs_:
            sub = sub[sub.store_id.isin(fs_)]
        if fc_:
            sub = sub[sub.category.isin(fc_)]
        w = kpi.weekly(sub)
        e1, e2 = kpi.wape(sub), kpi.wape(sub, "simple")
        bias_ = float((sub.pred.sum() - sub.actual.sum()) / sub.actual.sum()) if sub.actual.sum() else float("nan")
        kpis([("StockFlow error", pct(e1), "weekly, store × category", GREEN if e1 < e2 else RED),
              ("Simple average error", pct(e2), "same weeks and items"),
              ("Bias", "—" if pd.isna(bias_) else f"{bias_:+.0%}", "negative = predicted too low", SAFFRON if abs(bias_) > .15 else GREEN),
              ("Weeks", f"{len(w)}", f"{w.week_start.min()} to {w.week_start.max()}")])
        long_ = w.melt(id_vars="week_start", value_vars=["actual", "pred", "simple"], var_name="series", value_name="units")
        long_["series"] = long_.series.map({"actual": "Actual sales", "pred": "StockFlow prediction", "simple": "Simple 4-week average"})
        fig = px.line(long_, x="week_start", y="units", color="series", markers=True,
                      color_discrete_map={"Actual sales": "#1E2A33", "StockFlow prediction": TEAL, "Simple 4-week average": "#B8B2A7"},
                      labels={"week_start": "week starting", "units": "units", "series": ""},
                      title="Actual vs predicted units per week" + (f" · {', '.join(fs_)}" if fs_ else "") + (f" · {', '.join(fc_)}" if fc_ else ""))
        fig.update_traces(selector=dict(name="Actual sales"), line=dict(width=3))
        fig.update_traces(selector=dict(name="Simple 4-week average"), line=dict(dash="dot"))
        plot(fig, 360)
        st.dataframe(w.rename(columns={"week_start": "week starting", "pred": "StockFlow", "simple": "simple average",
                                       "error": "StockFlow error", "simple_error": "simple error"}),
                     hide_index=True, width="stretch",
                     column_config={c_: st.column_config.NumberColumn(format="%.0f") for c_ in ("StockFlow", "simple average", "actual")} |
                                   {c_: st.column_config.NumberColumn(format="percent") for c_ in ("StockFlow error", "simple error", "bias")})
        bycat = sub.groupby("category").apply(lambda d: pd.Series(dict(actual=d.actual.sum(), predicted=d.pred.sum(),
                                                                         stockflow_error=kpi.wape(d), simple_error=kpi.wape(d, "simple"))),
                                              include_groups=False).reset_index().sort_values("actual", ascending=False)
        st.markdown("**By category**")
        st.dataframe(bycat, hide_index=True, width="stretch",
                     column_config={"actual": st.column_config.NumberColumn(format="%.0f"), "predicted": st.column_config.NumberColumn(format="%.0f"),
                                    "stockflow_error": st.column_config.NumberColumn("StockFlow error", format="percent"),
                                    "simple_error": st.column_config.NumberColumn("simple error", format="percent")})
        st.download_button("⬇️ Weekly predicted vs actual (CSV)", safety.csv(sub), "predicted_vs_actual_weekly.csv", width="stretch")
        if e1 >= e2:
            note("In these weeks StockFlow's weekly prediction was <b>no better than the simple average</b>. Its transfers still use the "
                 "stockout-corrected speed (which the simple average can't give), but treat weekly sales predictions as indicative.")

    sec("Tracking across saved plans")
    hist = kpi.history() if not IN_BROWSER and not GUEST else pd.DataFrame()
    if len(hist) and hist.plan_date.nunique() >= 2:
        hs = hist[hist.store_id.isin(SCOPE)] if SCOPE else hist
        tr_ = hs.groupby("plan_date", as_index=False).agg(on_hand=("on_hand", "sum"), selling=("selling", "sum"), in_stock=("in_stock", "sum"),
                                                          at_risk=("at_risk", "sum"), old_units=("old_units", "sum"))
        tr_["in stock %"] = tr_.in_stock / tr_.selling.replace(0, float("nan"))
        tr_["old stock %"] = tr_.old_units / tr_.on_hand.replace(0, float("nan"))
        c1, c2 = st.columns(2)
        with c1:
            plot(px.line(tr_, x="plan_date", y="in stock %", markers=True, title="In-stock rate by plan day",
                         labels={"plan_date": ""}, color_discrete_sequence=[GREEN]).update_yaxes(tickformat=".0%"), 260)
        with c2:
            plot(px.line(tr_, x="plan_date", y="at_risk", markers=True, title="Store × items at risk of running out",
                         labels={"plan_date": "", "at_risk": "pairs"}, color_discrete_sequence=[RED]), 260)
        hv = kpi.history_vs_actual(hs, R["P"]["sales"])
        if len(hv):
            st.markdown("**Each plan's 7-day demand forecast vs what then sold** (actual is filled in once the data covers those 7 days)")
            st.dataframe(hv, hide_index=True, width="stretch", column_config={
                "predicted": st.column_config.NumberColumn(format="%.0f"), "actual": st.column_config.NumberColumn(format="%.0f")})
    else:
        st.caption("Every saved plan records these KPIs and its 7-day forecast. With a plan each day, trend charts and each plan's "
                   "forecast against actual sales appear here automatically.")


# ---- agents: AI assistants that investigate and draft; people approve
if "Agents" in TABS:
    with TABS["Agents"]:
        import agents as AG
        import ops_agents as OA
        sec("Agents")
        ag_on_, ag_key_ = AG.ai_status(SECRETS)
        st.caption("Each agent looks at today's plan, transfers and problems and writes drafts. Nothing is sent, recorded or ordered "
                   "until you approve a draft (you can edit it first). "
                   + ("AI is on: Claude writes the drafts." if ag_on_ and ag_key_ else
                      "AI is off: drafts are written from rules. An admin can turn AI on in Integrations."))
        cols_ = st.columns(3)
        for i_, (k_, (t_, d_, p_)) in enumerate([(k, v) for k, v in OA.AGENTS.items() if k not in ("whatif", "onboard")]):
            with cols_[i_ % 3]:
                st.markdown(f"**{t_}**  \n<span style='font-size:.85rem;opacity:.75'>{html.escape(d_)}</span>", unsafe_allow_html=True)
                if st.button(f"Run {t_.lower()}", key=f"ag_run_{k_}", disabled=not can_do(p_), width="stretch"):
                    with st.spinner(f"{t_} is working…"):
                        fn_ = {"brief": lambda: OA.run_brief(USER, PLAN_ID, SECRETS), "fix": lambda: OA.run_fixer(USER, SECRETS),
                               "chase": lambda: OA.run_chaser(USER, SECRETS), "buy": lambda: OA.run_buyer(USER, PLAN_ID, SECRETS)}[k_]
                        try:
                            n_ = fn_()
                            log("agent.run", k_, drafts=len(n_))
                            st.toast(f"{t_}: {len(n_)} drafts")
                        except Exception as e_:  # noqa: BLE001
                            st.error(f"{t_} stopped: {type(e_).__name__}: {e_}")
        if can_do("approve"):
            sec("What if…")
            with st.form("ag_whatif"):
                q_ = st.text_input("Ask a what-if question about today's plan",
                                   placeholder="e.g. close KOL-SL · demand +20% in Dolls · add a store like PAT-01 called PAT-02 · cover days 21")
                go_ = st.form_submit_button("🔮 Compare", type="primary")
            if go_ and q_.strip():
                with st.spinner("Planning twice (as is and with your change)…"):
                    try:
                        did_, sc_, a_, b_ = OA.run_whatif(q_.strip()[:500], USER, PLAN_ID, SECRETS)
                        log("agent.run", "whatif", q=q_[:200])
                        if b_:
                            rows_ = [("Trips", "trips", "{:,}"), ("Pieces moved", "pieces", "{:,}"), ("Expected benefit", "net_benefit", "₹{:,.0f}"),
                                     ("In stock after plan", "in_stock_after", "{:.1%}"), ("Items at risk after plan", "at_risk_after", "{:,}"),
                                     ("Forecast units, next 7 days", "pred7", "{:,.0f}"), ("Checks passed", "rules", "{}")]
                            st.markdown(f"**What if:** {html.escape(OA.describe(sc_))}")
                            st.dataframe(pd.DataFrame([{"": n, "Today": f.format(a_[k]), "Scenario": f.format(b_[k])} for n, k, f in rows_]),
                                         hide_index=True, width="stretch")
                    except Exception as e_:  # noqa: BLE001
                        st.error(str(e_))
        sec("Drafts waiting for you")
        dk_ = st.multiselect("Agent", list(OA.AGENTS) + ["reply"], format_func=lambda k: OA.AGENTS.get(k, ("WhatsApp replies",))[0],
                             key="ag_kind", placeholder="All agents")
        dd_ = AG.drafts(("pending",), dk_ or None)
        if len(dd_) and SCOPE:
            dd_ = dd_[dd_.location.isin(SCOPE) | (dd_.location == "")]
        if dd_.empty:
            st.success("Nothing waiting. Run an agent above.")
        for r_ in dd_.head(40).itertuples():
            perm_ = AG.ACTIONS[r_.action][0] if r_.action else "view"
            badge_ = "🤖 AI" if r_.source == "ai" else "📏 rules"
            with st.expander(f"{r_.title}  ·  {badge_}"):
                body_ = st.text_area("Draft (edit before approving)", r_.body, height=220, key=f"ag_b_{r_.id}")
                what_ = {"post_brief": "Approve posts it to the team channel (if set up).", "resolve_exceptions": "Approve resolves the exception with this note.",
                         "record_transfer": "Approve records the dispatch / receipt.", "send_whatsapp": "Approve sends this WhatsApp message.",
                         "approve_po": "Approve marks the purchase order approved (send the email yourself).",
                         "save_mapping": "Approve saves this column mapping for future uploads."}.get(r_.action, "Information only.")
                st.caption(what_)
                if r_.action == "approve_po":
                    po_ = pd.DataFrame((r_.payload or {}).get("lines", []))
                    if len(po_):
                        st.download_button("⬇️ Purchase order (CSV)", safety.csv(po_), f"{(r_.payload or {}).get('po_id', 'po')}.csv", key=f"ag_po_{r_.id}")
                a__, b__ = st.columns(2)
                if a__.button("✅ Approve", key=f"ag_ok_{r_.id}", type="primary", disabled=not can_do(perm_), width="stretch"):
                    try:
                        st.success(AG.decide(r_.id, USER, ROLE, True, body_, can=can_do, secrets=SECRETS))
                    except (PermissionError, ValueError) as e_:
                        st.error(str(e_))
                if b__.button("✖ Reject", key=f"ag_no_{r_.id}", width="stretch"):
                    AG.decide(r_.id, USER, ROLE, False)
                    st.rerun()
        with st.expander("Decided drafts"):
            hd_ = AG.drafts(("approved", "rejected", "done", "failed"))
            if len(hd_):
                st.dataframe(hd_[["id", "created_at", "agent", "title", "status", "decided_by", "decided_at", "result"]].head(200),
                             hide_index=True, width="stretch")


# ---- pilot tools: shadow mode (weeks 1-2) and the pilot scorecard (Operations tab)
if "Operations" in TABS:
    with TABS["Operations"]:
        import pilot as PILOT
        import shadow as SHADOW
        sec("Shadow mode: compare your own transfers with the plan")
        st.caption("For the first weeks of a pilot: keep planning transfers as you do today, upload what you actually moved "
                   "(columns like From, To, Item code, Qty, in any order), and see where StockFlow agrees, what it would have "
                   "added, and what it would not have done. Nothing is released.")
        sh_up_ = st.file_uploader("What your team moved (CSV or Excel)", type=["csv", "xlsx", "xls"], key="sh_up")
        if sh_up_ is not None and PLAN_ID:
            try:
                raw_ = pd.read_csv(sh_up_) if sh_up_.name.lower().endswith(".csv") else pd.read_excel(sh_up_)
                Rx_ = st.session_state.get("R")
                res_ = SHADOW.compare(raw_, PLAN_ID, skipped=Rx_.get("skipped") if isinstance(Rx_, dict) else None)
                s_ = res_["summary"]
                kpis([("Plan matched", f"{s_['agreement']:.0%}" if s_["agreement"] is not None else "—", f"{s_['agreed_pieces']:,} of {s_['plan_pieces']:,} pieces"),
                      ("Plan only", f"{s_['missed_lines']:,} lines", f"{s_['missed_pieces']:,} pcs · est. {M(s_['missed_value'])}", SAFFRON),
                      ("Your moves only", f"{s_['planner_only_lines']:,} lines", f"{s_['planner_only_pieces']:,} pieces")])
                st.markdown(html.escape(SHADOW.text(s_)))
                st.dataframe(res_["lines"].head(500), hide_index=True, width="stretch",
                             column_config={"value": st.column_config.NumberColumn("plan's est. value", format="₹%d")})
                st.download_button("⬇️ Comparison (CSV)", safety.csv(res_["lines"]), f"shadow_{PLAN_ID}.csv", key="sh_dl")
                if can_do("approve") and st.button("Save this comparison to the pilot record", key="sh_save"):
                    SHADOW.save(PLAN_ID, s_, USER)
                    log("pilot.shadow", PLAN_ID, agreement=s_["agreement"])
                    st.success("Saved.")
            except Exception as e_:  # noqa: BLE001
                st.error(f"Couldn't read that file: {e_}")
        sh_h_ = SHADOW.history()
        if len(sh_h_):
            with st.expander(f"Saved comparisons ({len(sh_h_)})"):
                st.dataframe(sh_h_[[c for c in ("plan_id", "at", "agreement", "missed_lines", "missed_value", "planner_only_lines", "by") if c in sh_h_]],
                             hide_index=True, width="stretch")

        sec("Pilot scorecard")
        ps_ = PILOT.setup()
        all_st_ = sorted(R["pl"].st.index) if isinstance(st.session_state.get("R"), dict) else []
        with st.expander("Pilot set-up" + (f" · started {ps_['start']}" if ps_.get("start") else ""), expanded=not ps_.get("start")):
            if not can_do("settings"):
                st.caption("An admin sets up the pilot.")
            else:
                with st.form("pilot_setup"):
                    a_, b_ = st.columns(2)
                    start_ = a_.date_input("Pilot start", date.fromisoformat(ps_["start"]) if ps_.get("start") else date.today())
                    hb_ = b_.number_input("Planner hours a week on transfers before the pilot", 0.0, 500.0, float(ps_.get("hours_before") or 0.0))
                    pst_ = st.multiselect("Pilot stores", all_st_, default=[s for s in ps_.get("pilot_stores", []) if s in all_st_])
                    cst_ = st.multiselect("Comparison stores (similar, not in the pilot)", all_st_,
                                          default=[s for s in ps_.get("comparison_stores", []) if s in all_st_])
                    if st.form_submit_button("Save pilot set-up", type="primary"):
                        try:
                            PILOT.save_setup(start_, pst_, cst_, hb_, ps_.get("hours_now"), USER)
                            log("pilot.setup", "", pilot=len(pst_), comparison=len(cst_))
                            st.rerun()
                        except ValueError as e_:
                            st.error(str(e_))
        if ps_.get("start"):
            hn_ = st.number_input("Planner hours on transfers this week", 0.0, 500.0, float(ps_.get("hours_now") or 0.0), key="pilot_hn")
            if hn_ != (ps_.get("hours_now") or 0.0) and st.button("Save hours", key="pilot_hn_save"):
                PILOT.set_hours_now(hn_, USER)
                st.rerun()
            sc_ = PILOT.scorecard()
            st.markdown(f"**{html.escape(PILOT.verdict(sc_))}**")
            st.dataframe(pd.DataFrame([{"": "✅" if r["passed"] else ("❌" if r["passed"] is False else "…"), "Measure": r["measure"],
                                        "Pass mark": r["target"], "Pilot": r["pilot"], "Comparison": r["comparison"], "Result": r["result"],
                                        "Note": r["detail"]} for r in sc_["rows"]]), hide_index=True, width="stretch")
