"""StockFlow app: upload a brand's data pack, get the day's stock moves, their money, and the trips to approve.

Start:  streamlit run app.py
"""
import html
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import auth
import chat
import engine as E
import feedback as fb
import insights as ins
import notify
import schema

HERE = Path(__file__).parent
BRANDS = sorted((HERE / "brands").glob("*.json"))
DEMO = HERE / "data" / "demo"
IN_BROWSER = sys.platform == "emscripten"   # running as a static web page (stlite): no server, no file downloads
st.set_page_config(page_title="StockFlow · stock planning for apparel retail", page_icon="📦", layout="wide",
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
                f'<div class="sf-sub">Daily stock planning for apparel retail</div></div>'
                f'<div class="sf-chip">{html.escape(extra or cfg["brand"]["name"])}</div></div>', unsafe_allow_html=True)


# ----------------------------------------------------------------------------- sign in
USERS = auth.users(st.secrets if hasattr(st, "secrets") else None)
if USERS and "user" not in st.session_state:
    css()
    topbar(schema.DEFAULTS, "Sign in")
    st.markdown('<div class="hero"><h1>Welcome back</h1><p>Sign in to see today\'s stock plan.</p></div>', unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 2, 1])
    with mid, st.form("login"):
        u = st.text_input("User name")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary", width="stretch"):
            rec = USERS.get(u.strip())
            if rec and auth.check_password(p, rec.get("password", "")):
                st.session_state.user, st.session_state.role = u.strip(), rec.get("role", "viewer")
                st.session_state.scope = [s.strip() for s in str(rec.get("stores", "")).split(",") if s.strip()]
                st.rerun()
            st.error("User name or password not recognised.")
    st.stop()
USER = st.session_state.get("user", "demo")
ROLE = st.session_state.get("role", "admin" if not USERS else "viewer")
OPEN = not USERS and not IN_BROWSER


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
    dis = not auth.can(ROLE, "admin")
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


def start(source, cfg, label):
    tmp = None
    try:
        if source is None:
            tmp = Path(tempfile.mkdtemp(prefix="stockflow_in_"))
            over = unpack(st.session_state.ups, tmp)
            cfg = schema.merge(cfg, over) if over else cfg
            source = tmp
        with st.spinner("Checking your data, measuring selling speed, planning and pricing every move…"):
            R = run_engine(source, cfg)
        st.session_state.R, st.session_state.label = R, label
        st.session_state.run_id = fb.log_run(R, label, USER)
        st.session_state.chat = []
        st.session_state.pop("ctx", None)
        st.rerun()
    except E.DataError as e:
        st.error("**The data isn't quite what StockFlow needs.** Please fix:\n\n" + "\n".join(f"- {p}" for p in str(e).splitlines()))
    except Exception as e:  # noqa: BLE001
        st.error(f"Something went wrong while planning: {type(e).__name__}: {e}")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def data_panel(key="main"):
    with st.container(border=True):
        st.markdown('<div class="sf-h" style="font-weight:800;font-size:1.05rem">Plan with your own data</div>', unsafe_allow_html=True)
        ups = st.file_uploader("Drop a zip, an Excel workbook or the CSV files (add config.json for brand settings)",
                               type=["zip", "csv", "xlsx", "xlsm", "json"], accept_multiple_files=True, key="ups")
        brand = st.selectbox("Settings profile", ["Standard settings"] + [b.stem for b in BRANDS], key=f"brand_{key}")
        base = schema.load_config(HERE / "brands" / f"{brand}.json" if brand != "Standard settings" else None, fb.load_settings())
        with st.expander("⚙️ Settings"):
            settings_panel(base)
        if st.button("▶️  Build my plan", type="primary", disabled=not ups, width="stretch", key=f"go_{key}"):
            start(None, base, ", ".join(f.name for f in ups)[:120])


def demo_button(key, label="✨  Try the demo brand", primary=True):
    if st.button(label, type="primary" if primary else "secondary", width="stretch", key=f"demo_{key}", disabled=not DEMO.exists()):
        start(DEMO, schema.load_config(HERE / "brands" / "demo.json", fb.load_settings()), "Demo Threads (fictional brand)")


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
                    st.markdown(f"**{rec['who']}** · {rec['role']}  \n<span style='color:{MUTED}'>{html.escape(rec['text'])}</span>",
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
if "R" not in st.session_state:
    css()
    topbar(schema.DEFAULTS, "Demo" if OPEN or IN_BROWSER else USER)
    st.markdown("""<div class="hero"><h1>The right stock in the right store, in the right size, every morning.</h1>
<p>Upload yesterday's sales and stock. StockFlow gives you a short list of trips to approve, with what each one earns and what it costs to move.</p>
<div class="meta"><span>👗 Built for apparel & ethnic wear</span><span>🏬 5–100 stores</span><span>🔒 Your data stays yours</span></div></div>""",
                unsafe_allow_html=True)
    if OPEN:
        note("🔓 <b>Open demo:</b> no sign-in is set up yet. Use the demo brand, or add users before uploading real data.")
    if IN_BROWSER:
        note("🔒 This copy runs entirely in your browser: files you upload never leave this device. Ratings and receipts last until you close the page.", "ok")
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<div class="sf-h" style="font-weight:800;font-size:1.05rem;margin-bottom:.3rem">See it in 20 seconds</div>'
                    '<p style="color:#5E6875;margin:0 0 .7rem">A fictional 9-store brand with two warehouses and an outlet.</p>',
                    unsafe_allow_html=True)
        demo_button("hero")
        st.markdown("""<div style="margin-top:1rem;display:grid;gap:.8rem">
<div class="step"><b class="n">1</b><div><b>Upload four sheets</b><br><span style="color:#5E6875">stores, items, sales and stock. More sheets switch on more features.</span></div></div>
<div class="step"><b class="n">2</b><div><b>Get priced trips</b><br><span style="color:#5E6875">Refills, size-set fixes, old stock to faster stores, capped returns.</span></div></div>
<div class="step"><b class="n">3</b><div><b>Approve in ten minutes</b><br><span style="color:#5E6875">About 25 decisions, then transfer orders for your ERP.</span></div></div></div>""",
                    unsafe_allow_html=True)
    with right:
        data_panel("landing")
    sec("Why brands use it")
    st.markdown("""<div class="grid">
<div class="card"><div class="ic">💰</div><h4>Every move is priced</h4><p>Expected extra sales against freight and handling. Moves that don't pay are left out.</p></div>
<div class="card"><div class="ic">🧩</div><h4>Size sets stay whole</h4><p>Missing core sizes are filled first, and broken runs of old stock move together.</p></div>
<div class="card"><div class="ic">🚚</div><h4>Fewer, fuller trips</h4><p>One line per trip says why it goes. No more hundreds of single-piece transfers.</p></div>
<div class="card"><div class="ic">🎉</div><h4>Knows your season</h4><p>Wedding dates and festival weeks by region, from your own calendar.</p></div></div>""", unsafe_allow_html=True)
    with st.expander("📄 What data do I need?"):
        data_guide("landing")
    st.stop()

# ============================================================================= results
R = st.session_state.R
cfg = R["cfg"]
css(cfg["brand"].get("primary_color", TEAL) if cfg["brand"].get("primary_color", "").startswith("#") else TEAL)
M = lambda v: E.money(v, cfg)
pl, mv, ex, val, tr, O = R["pl"], R["moves"], R["issues"], R["checks"], R["trips"], R["opportunity"]
P, g, stores = R["P"], pl.g, pl.st
SCOPE = st.session_state.get("scope") or []
if SCOPE:  # a user limited to some stores sees only their trips
    mv = mv[mv.from_id.isin(SCOPE) | mv.to_id.isin(SCOPE)]
    tr = tr[tr.from_id.isin(SCOPE) | tr.to_id.isin(SCOPE)]
L = ins.leadership(R)
topbar(cfg, f"{USER} · {ROLE}")
allp = bool((val.status == "PASS").all())
st.markdown(f"""<div class="hero sm"><h1>Plan for {pl.run:%A, %d %b %Y}</h1>
<p>{html.escape(cfg['brand']['name'])} · stock as of {P['today']:%d %b} · {len(stores)} stores · warehouses {html.escape(', '.join(pl.whs))}</p>
<div class="meta"><span>🚚 {len(tr)} trips</span><span>💰 net {html.escape(M(O['net']))}</span>
<span>{'✅ all ' + str(len(val)) + ' rules passed' if allp else '⚠️ some rules failed'}</span></div></div>""", unsafe_allow_html=True)
if OPEN:
    note("🔓 <b>Open demo:</b> anyone with the link can see this. Set up sign-in before using real data.")
with st.expander("📂  New data or settings"):
    data_panel("results")
    demo_button("results", "✨  Reload the demo brand", primary=False)

T = st.tabs(["Overview", "Money", "Trips", "Approve", "Issues", "Stores", "Track", "Ask", "Feedback", "Data", "How it works"])

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
    if R["xlsx"]:
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
    can_ok = auth.can(ROLE, "approver")
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
    orders = R["orders"]
    orders = orders[orders.to_number.isin(ok_to) & orders.to_number.isin(tr.to_number)] if len(orders) else orders
    t_ok = tr[tr.to_number.isin(ok_to)]
    with st.container(border=True):
        kpis([("Approved", f"{len(t_ok)} of {len(tr)} trips", f"{int(t_ok.pieces.sum()):,} pieces"), ("Net gain", M(t_ok.net_benefit.sum()), "of approved trips", GREEN)])
        if can_ok:
            a, b = st.columns(2)
            a.download_button("⬇️ Transfer orders (CSV)", orders.to_csv(index=False).encode(), f"transfer_orders_{pl.run:%Y-%m-%d}.csv",
                              type="primary", width="stretch")
            if b.button("📤 Release & send WhatsApp alerts", width="stretch", disabled=orders.empty):
                fb.release_orders(orders, USER, st.session_state.run_id)
                st.session_state.released = orders.copy()
                st.session_state.wa_sent = False
                st.success(f"Released {orders.to_number.nunique()} transfer orders. Record receipts on the Track tab.")
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
        sn["band"] = pd.cut(sn.age, [-1, 30, 90, a, 365, 100000], labels=["< 1 month", "1–3 months", f"3 mo–{a} d", f"{a} d–1 yr", "> 1 year"])
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
                st.markdown(f"**{r.to_number}** {r.from_id} → {r.to_id} · {r.pieces} pcs  \n<span style='color:{MUTED}'>{html.escape(r.reason)}</span>",
                            unsafe_allow_html=True)

# ---- track
SCOL = {"Waiting to leave": MUTED, "Late leaving": RED, "In transit": TEAL, "Late arriving": RED,
        "Received short": SAFFRON, "Received over": SAFFRON, "Received": GREEN}


def can_mark(loc):
    return auth.can(ROLE, "approver") or (bool(SCOPE) and loc in SCOPE)


def movement_alert(t, kind):
    """Tell the people at both ends; automatic with the WhatsApp API, else queue one-tap buttons."""
    recs = notify.movement_recipients(R, t, kind)
    if not recs:
        return
    W = notify.configured(st.secrets if hasattr(st, "secrets") else None)
    if W:
        res = notify.send_all(W, recs, USER)
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
                        row = fb.tracking()[0].set_index("to_number").loc[r.to_number]
                        movement_alert(pd.Series({**row.to_dict(), "to_number": r.to_number}), "left")
                        st.rerun()
                    with c2.popover("Sent a different quantity?", width="stretch"):
                        ed = st.data_editor(ln[["sku_id", "planned"]].rename(columns={"planned": "sent"}), key=f"eds_{r.to_number}",
                                            hide_index=True, disabled=["sku_id"], width="stretch")
                        if st.button("Save what was sent", key=f"sv_s_{r.to_number}"):
                            fb.mark_dispatched(r.to_number, USER, dict(zip(ed.sku_id, ed.sent.astype(int))), "edited", tz)
                            row = fb.tracking()[0].set_index("to_number").loc[r.to_number]
                            movement_alert(pd.Series({**row.to_dict(), "to_number": r.to_number}), "left")
                            st.rerun()
                elif r.sent_lines > 0 and r.got_lines == 0 and can_mark(r.to_location):
                    c1, c2 = st.columns([1, 1])
                    if c1.button("📦 Mark as received (all of it)", key=f"rcv_{r.to_number}", type="primary", width="stretch"):
                        fb.mark_received(r.to_number, USER, tz=tz)
                        st.rerun()
                    with c2.popover("Received a different quantity?", width="stretch"):
                        ed = st.data_editor(ln[["sku_id", "sent"]].assign(received=ln.sent), key=f"edr_{r.to_number}",
                                            hide_index=True, disabled=["sku_id", "sent"], width="stretch")
                        if st.button("Save what arrived", key=f"sv_r_{r.to_number}"):
                            fb.mark_received(r.to_number, USER, dict(zip(ed.sku_id, ed.received.astype(int))), "counted", tz)
                            row = fb.tracking()[0].set_index("to_number").loc[r.to_number]
                            if int(row.received) != int(row.sent):
                                movement_alert(pd.Series({**row.to_dict(), "to_number": r.to_number}), "received")
                            st.rerun()
                elif r.status in ("Received short", "Received over"):
                    with st.expander("See the lines that differ"):
                        st.dataframe(ln[ln.received != ln.sent.where(ln.sent > 0, ln.planned)], hide_index=True, width="stretch")
        if len(view) > 60:
            st.caption(f"Showing 60 of {len(view)}. Use the filter or search.")
        st.download_button("⬇️ Tracking report (CSV)", trl.to_csv(index=False).encode(), "stock_movement_tracking.csv", width="stretch")
    if auth.can(ROLE, "approver"):
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
    if auth.can(ROLE, "admin") and not ai:
        with st.expander("Turn on AI answers"):
            st.caption("Sends the plan report (trips, moves, issues, stock figures; never customer or staff data) to the AI provider. "
                       "Check your data-processing agreement covers this first.")
            k = st.text_input("Anthropic API key (blank = use the server's key)", type="password")
            if st.checkbox("Sending the plan report to the AI provider is allowed") and st.button("Turn on"):
                if k:
                    st.session_state.api_key = k.strip()
                st.session_state.ai_ok = True
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
                st.success("Thank you!")
    if not r.empty:
        kpis([("Average rating", f"{r.stars.mean():.1f} ★", f"{len(r)} reviews", SAFFRON), ("Moves right", f"{r.moves_right.mean():.1f}/5", ""),
              ("Easy", f"{r.easy.mean():.1f}/5", ""), ("Saves time", f"{r.saves_time.mean():.1f}/5", "")])
    sug = fb.suggested_settings(cfg)
    if len(sug):
        sec("Suggested settings from feedback")
        st.dataframe(sug.drop(columns="setting"), hide_index=True, width="stretch")
        if auth.can(ROLE, "admin"):
            a, b = st.columns(2)
            if a.button("✅ Use these from the next run", type="primary", width="stretch"):
                over = fb.load_settings()
                for row in sug.itertuples():
                    s1, k1 = row.setting.split(".")
                    over.setdefault(s1, {})[k1] = int(row.suggested)
                fb.save_settings(over)
                st.success("Saved.")
            if b.button("↩️ Back to standard settings", width="stretch"):
                fb.reset_settings()
    if not runs.empty:
        with st.expander(f"Run history ({len(runs)})"):
            st.dataframe(runs[::-1], hide_index=True, width="stretch")

# ---- data
with T[9]:
    data_guide("tab")

# ---- how it works
with T[10]:
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
