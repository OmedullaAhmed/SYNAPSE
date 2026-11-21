"""Stock Mover app: upload a data pack, get the day's stock moves explained in plain language.

Start:  streamlit run app.py   (or double-click "Start Stock Mover.bat")
"""
import io
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import chat
import feedback as fb
import insights as ins
import stock_mover as sm
from insights import inr

st.set_page_config(page_title="Stock Mover", page_icon="📦", layout="wide", initial_sidebar_state="collapsed")

# ----------------------------------------------------------------------------- look & feel (desktop + phone)
st.markdown("""
<style>
.block-container{padding-top:1.4rem;padding-bottom:5rem;max-width:1180px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.6rem;margin:.3rem 0 1rem}
.kpi{border:1px solid rgba(128,128,128,.25);border-radius:14px;padding:.75rem .9rem;background:rgba(127,127,127,.06)}
.kpi .l{font-size:.78rem;opacity:.75;text-transform:uppercase;letter-spacing:.03em}
.kpi .v{font-size:1.5rem;font-weight:700;line-height:1.25;margin:.1rem 0}
.kpi .s{font-size:.78rem;opacity:.72}
.ins{border-left:5px solid var(--c);padding:.6rem .85rem;margin:.5rem 0;border-radius:8px;background:rgba(127,127,127,.06);line-height:1.45}
.pill{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.75rem;font-weight:600;color:#fff}
.stTabs [data-baseweb="tab-list"]{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:thin;gap:.2rem}
.stTabs [data-baseweb="tab"]{white-space:nowrap;padding:0 .55rem}
@media (max-width:640px){
  .block-container{padding-left:.65rem;padding-right:.65rem;padding-top:.8rem}
  h1{font-size:1.4rem!important} h2{font-size:1.15rem!important} h3{font-size:1.02rem!important}
  .kpis{grid-template-columns:1fr 1fr;gap:.45rem}
  .kpi{padding:.55rem .65rem}
  .kpi .v{font-size:1.12rem}
  .kpi .s{font-size:.7rem}
}
</style>""", unsafe_allow_html=True)

TYPE_COLORS = {sm.T_WH: "#2a9d8f", sm.T_S2S: "#e9c46a", sm.T_DISP: "#9b5de5", sm.T_AGE: "#f4a261", sm.T_PULL: "#e76f51"}
TYPE_PLAIN = {
    sm.T_WH: ("Send from warehouse", "Stores that will run out soon get stock from the warehouse. When there isn't enough "
                                     "for everyone, the store that will run out first is served first."),
    sm.T_S2S: ("Move between stores", "When the warehouse has none, we borrow from a store that has plenty, but only if it "
                                      "still keeps at least 30 days' worth."),
    sm.T_DISP: ("Fill the display", "Every store must show a minimum number of pieces in each category."),
    sm.T_AGE: ("Old stock to where it sells", "Stock that has sat for months goes to a store where the same item sells well."),
    sm.T_PULL: ("Back to warehouse", "Old stock no store is selling returns to the warehouse, freeing shelf space."),
}
ISSUE_PLAIN = {
    "Stock records disagree": ("Two stock records don't match", "We could plan to move stock that isn't really there.",
                               "Trusted the physical weekly count, didn't move this stock, asked the store to recount."),
    "Negative stock": ("A store shows less than zero pieces", "You can't hold minus pieces, so it's a recording mistake.",
                       "Used the weekly count (or zero) and asked for a recount."),
    "Hidden stockout": ("Sizes that look slow but just weren't on the shelf",
                        "Zero sales because of zero stock is not zero demand. These sales are being lost quietly.",
                        "Measured selling speed only on days in stock, and refilled them."),
    "Under-stocked store": ("A busy store with half-empty shelves", "Customers can't find their size and walk out.",
                            "This store gets priority in today's refill."),
    "Over capacity": ("A store holding more than it can", "Either the record is wrong or the store is overloaded.",
                      "No more stock is sent until it's sorted."),
    "Warehouse shortfall": ("Not enough stock in the warehouse", "Stores need more than the warehouse holds as wedding season starts.",
                            "Shared by who runs out first, plus safe moves between stores."),
    "Dead style": ("A design that never sells", "It ties up money and shelf space.", "Pulled back. Buying should stop re-ordering it."),
    "New launch": ("A new design with little history", "Few sales so far doesn't mean slow; it's just new.",
                   "Selling speed measured only on days it was on the shelf."),
    "New store": ("A store that just opened", "Too little history to know what it will sell.",
                  "Used similar stores as a guide until its own sales build up."),
    "Below minimum display after plan": ("A display still below minimum", "No safe source of stock.", "Flagged to buying."),
    "Lead time missing": ("No delivery time for a zone", "Needed to plan arrival dates.", "Assumed the longest delivery time."),
}
RULE_PLAIN = {
    "Every move has a positive quantity and different from/to": "Each move ships at least one piece to a different place.",
    "Every move is explained in one line": "Each move has a one-line reason.",
    "Stores only give stock they physically hold (not in-transit)": "Stores never give away stock they don't have on the shelf.",
    "Warehouse never sends more than on hand + inbound due": "The warehouse never ships more than it has.",
    "No receiving store ends above its capacity (on hand + in transit + incoming)": "No store gets more than it can hold.",
    "Giving stores stay at or above minimum display per category": "Stores giving stock still keep their minimum display.",
    "S++ flagships never give away their top-20 styles": "Super-flagships keep their 20 best designs.",
    "No store-to-store move leaves the giver under 30 days of style cover": "A giving store keeps at least 30 days' worth.",
    "In-transit stock counted: never replenish a size already covered by stock + transit": "Stock already on a truck counts, so nothing is sent twice.",
    "No store both gives and receives the same SKU": "No store sends and receives the same item.",
    "Moves above Rs 1,00,000 at MRP are flagged for approval": "Moves over ₹1 lakh are flagged for sign-off.",
}
EXAMPLES = ["What are the key insights today?", "Which stores need the most stock?", "Why is MZF-01 a problem?",
            "What needs approval?", "What happened with the KNP-03 sherwanis?", "Tell me about KUR-04"]


def style(fig, h=320, legend=True):
    fig.update_layout(height=h, margin=dict(l=4, r=4, t=44, b=4), font=dict(size=12), title_font_size=14,
                      legend=dict(orientation="h", yanchor="top", y=-0.18, x=0, title="") if legend else None,
                      showlegend=legend)
    return fig


def plot(fig, h=320, legend=True):
    st.plotly_chart(style(fig, h, legend), use_container_width=True, config={"displayModeBar": False, "responsive": True})


def kpi_cards(items):
    st.markdown('<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
        for l, v, s in items) + "</div>", unsafe_allow_html=True)


def insight(text, color="#2a9d8f"):
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    st.markdown(f'<div class="ins" style="--c:{color}">{html}</div>', unsafe_allow_html=True)


# ----------------------------------------------------------------------------- data in
def unpack(uploads, dest):
    for f in uploads:
        name = Path(f.name).name
        data = f.getvalue()
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.infolist():
                    p = Path(m.filename)
                    if m.is_dir() or p.is_absolute() or ".." in p.parts or p.suffix.lower() not in (".csv", ".xlsx", ".xlsm"):
                        continue
                    out = dest / "zip" / p
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(m))
        elif name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            (dest / name).write_bytes(data)


def run_engine(source, settings):
    old = dict(sm.CFG)
    sm.CFG.update(settings)
    try:
        R = sm.run(source, Path(tempfile.mkdtemp(prefix="stockmover_out_")))
        R["cfg"] = dict(sm.CFG)
        return R
    finally:
        sm.CFG.clear()
        sm.CFG.update(old)


def data_panel(expanded):
    saved = fb.load_settings()
    ups = st.file_uploader("Upload the data pack: a zip of the CSVs, the Excel workbook, or the CSV files",
                           type=["zip", "csv", "xlsx", "xlsm"], accept_multiple_files=True, key="ups")
    with st.expander("⚙️ Settings" + (" (tuned by feedback)" if fb.SETTINGS.exists() else "")):
        vals = {}
        for k, (lo, hi, step, name) in fb.TUNABLE.items():
            vals[k] = st.slider(name, lo, hi, int(saved[k]), step=1, key=f"set_{k}")
        st.caption("Business rules (30-day cover for giving stores, minimum display, capacity, flagship protection) never change.")
    with st.expander("📄 Which files do I need?"):
        st.dataframe(pd.DataFrame([(t, ", ".join(c)) for t, c in sm.REQUIRED.items()], columns=["table", "columns"]),
                     hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    go_btn = c1.button("▶️ Run Stock Mover", type="primary", disabled=not ups, use_container_width=True)
    sample = c2.button("Try the sample data", use_container_width=True, disabled=not sm.DEFAULT_DATA.exists())
    if go_btn or sample:
        tmp = Path(tempfile.mkdtemp(prefix="stockmover_in_"))
        try:
            if go_btn:
                unpack(ups, tmp)
                source, label = tmp, ", ".join(f.name for f in ups)[:120]
            else:
                source, label = sm.DEFAULT_DATA, "sample data pack"
            with st.spinner("Reading the data, measuring how fast every size sells, planning moves… (about 30 seconds)"):
                R = run_engine(source, vals)
            st.session_state.R = R
            st.session_state.label = label
            st.session_state.run_id = fb.log_run(R, label, vals)
            st.session_state.ctx = chat.build_context(R)
            st.session_state.chat = []
            st.rerun()
        except sm.DataError as e:
            st.error("**The data isn't quite what Stock Mover needs.** Please fix:\n\n" + "\n".join(f"- {p}" for p in str(e).splitlines()))
        except Exception as e:  # noqa: BLE001
            st.error(f"Something went wrong while planning: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if "R" not in st.session_state:
    st.title("📦 Stock Mover")
    st.markdown("**Right stock. Right store. Right size.**")
    st.markdown("Every day, some stock is in the **wrong place**: one store runs out of a size customers want, while "
                "another has the same size sitting unsold. Stock Mover finds those problems and writes a **ready-to-approve list**: "
                "🚚 send from the warehouse · 🔁 move between stores · ↩️ bring back to the warehouse.")
    data_panel(True)
    st.stop()

# ----------------------------------------------------------------------------- results
R = st.session_state.R
pl, mv, ex, val, alerts = R["pl"], R["moves"].copy(), R["issues"], R["checks"], R["alerts"]
P, g, stores, WH = R["P"], pl.g, pl.st, R["warehouse"]
mv["kind"] = mv.type.map(lambda t: TYPE_PLAIN[t][0])
L = ins.leadership(R)

st.title(f"Stock plan · {pl.run:%a %d %b %Y}")
st.caption(f"{st.session_state.label} · stock as of {P['today']:%d %b %Y} · {len(stores)} stores + {WH}")
with st.expander("📂 Load new data or change settings"):
    data_panel(False)

T = st.tabs(["⭐ Leaders", "📋 Plan", "🔍 Problems", "🏬 Stores", "✅ Approve", "💬 Ask", "📝 Feedback", "ℹ️ Method"])

# ---- Leaders: key insights only
with T[0]:
    st.subheader("Key insights")
    kpi_cards(L["kpis"])
    for color, text in L["insights"]:
        insight(text, color)
    st.markdown("#### Decisions needed")
    for d in L["decisions"]:
        st.markdown(f"- {d}")
    city = L["city"].reset_index(names="city").melt("city", var_name="", value_name="₹ lakh")
    plot(px.bar(city, x="city", y="₹ lakh", color="", barmode="group", title="Impact by city (₹ lakh at MRP)",
                color_discrete_sequence=["#2a9d8f", "#f4a261"]), 300)
    passed = int((val.status == "PASS").sum())
    r = fb.ratings()
    rate = f" · team rating {r.stars.mean():.1f}★ from {len(r)} reviews" if not r.empty else ""
    st.caption(f"Plan health: {passed}/{len(val)} business rules passed{rate}.")
    if R["xlsx"]:
        st.download_button("⬇️ Full plan (Excel)", Path(R["xlsx"]).read_bytes(), file_name=Path(R["xlsx"]).name, key="xl1")

# ---- Plan
with T[1]:
    kpi_cards([("Moves", f"{len(mv):,}", f"{mv.from_id.nunique()} senders"), ("Pieces", f"{int(mv.qty.sum()):,}", "to move"),
               ("Value", inr(mv.value_mrp.sum()), "at MRP"),
               ("Urgent", f"{int((mv.priority == 'High').sum())}", "lines: store runs out before delivery")])
    high = mv[mv.priority == "High"]
    ev = pl.season.next_event(stores.state.iloc[0], pl.run, 45)
    st.markdown("#### Today in plain words")
    lines = []
    if len(high):
        lines.append(f"🔴 **{high.sku_id.nunique()} items in {high.to_id.nunique()} stores will run out before a normal delivery.** "
                     f"{int(high.qty.sum()):,} pieces are on their way, arriving by {high.arrives.max():%d %b}.")
    if ev:
        lines.append(f"💍 **Busy season ahead:** {ev}. Wedding dates sell "
                     + ", ".join(f"{(l.get('wedding date', 1) - 1) * 100:.0f}% more in {s}" for s, l in pl.season.lift.items()) + ".")
    s2s = mv[mv.type == sm.T_S2S]
    if len(s2s):
        lines.append(f"🔁 **{int(s2s.qty.sum())} pieces move between stores** where the warehouse ran short, only from stores keeping 30+ days.")
    old = mv[mv.type.isin([sm.T_AGE, sm.T_PULL])]
    if len(old):
        lines.append(f"🕸️ **{int(old.qty.sum())} old pieces** are moving: {int(mv[mv.type == sm.T_AGE].qty.sum())} to stores where "
                     f"they sell, {int(mv[mv.type == sm.T_PULL].qty.sum())} back to the warehouse.")
    for l in lines:
        st.markdown(l)
    by = mv.groupby("type").agg(pieces=("qty", "sum")).reset_index()
    by["what"] = by.type.map(lambda t: TYPE_PLAIN[t][0])
    plot(px.bar(by.sort_values("pieces"), x="pieces", y="what", color="type", color_discrete_map=TYPE_COLORS, orientation="h",
                title="Pieces by kind of move", labels={"what": ""}), 280, legend=False)
    st.markdown("#### What each kind of move means")
    for t, (name, text) in TYPE_PLAIN.items():
        n = mv[mv.type == t]
        st.markdown(f'<span class="pill" style="background:{TYPE_COLORS[t]}">{int(n.qty.sum()):,} pcs</span> **{name}**: {text}',
                    unsafe_allow_html=True)
    st.markdown("#### Where stock goes")
    pick = st.multiselect("Show", list(TYPE_PLAIN), default=[sm.T_WH, sm.T_S2S, sm.T_DISP],
                          format_func=lambda t: TYPE_PLAIN[t][0])
    f = mv[mv.type.isin(pick)]
    if len(f):
        city_of = lambda x: x if x == WH else stores.city[x]
        cc = f.assign(fc=f.from_id.map(city_of), tc=f.to_id.map(city_of)).groupby(["fc", "tc"]).qty.sum().reset_index()
        dot = ["digraph{rankdir=LR;bgcolor=transparent;node[shape=box style=\"rounded,filled\" fillcolor=\"#f1faee\" fontname=Helvetica];",
               f'"{WH}"[fillcolor="#2a9d8f" fontcolor=white label="Warehouse\\n{WH}"];']
        for r in cc.itertuples():
            lab = f"{r.qty} within city" if r.fc == r.tc else f"{r.qty}"
            dot.append(f'"{r.fc}"->"{r.tc}"[label="{lab}" fontsize=10 penwidth={1 + min(r.qty / cc.qty.max() * 6, 6):.1f}];')
        st.graphviz_chart("\n".join(dot) + "}", use_container_width=True)
        with st.expander("Store-by-store flow diagram"):
            agg = f.groupby(["from_id", "to_id", "type"]).qty.sum().reset_index()
            src, dst = sorted(set(agg.from_id)), sorted(set(agg.to_id))
            ix = {("f", s): i for i, s in enumerate(src)} | {("t", d): len(src) + i for i, d in enumerate(dst)}
            rgba = lambda h: f"rgba({int(h[1:3], 16)},{int(h[3:5], 16)},{int(h[5:7], 16)},0.55)"
            fig = go.Figure(go.Sankey(node=dict(label=src + dst, pad=10, thickness=12, color="#577590"),
                                      link=dict(source=[ix[("f", s)] for s in agg.from_id], target=[ix[("t", d)] for d in agg.to_id],
                                                value=agg.qty, color=[rgba(TYPE_COLORS[t]) for t in agg.type])))
            plot(fig, max(420, 20 * (len(src) + len(dst))), legend=False)
    st.markdown("#### Area manager alerts")
    st.caption("Each area manager reads at most 5 alerts a week, so these are their top 5.")
    for am_name, grp in alerts.groupby("area_manager", sort=True):
        with st.expander(am_name):
            for r in grp.itertuples():
                st.markdown(f"{r.rank}. {r.alert}")

# ---- Problems
with T[2]:
    st.markdown("Problems found in the data: **what they mean**, **why they matter**, and **what we did**.")
    weekly, sales = R["weekly"], R["sales"]
    order = ["Stock records disagree", "Negative stock", "Under-stocked store", "Hidden stockout", "Warehouse shortfall",
             "Over capacity", "Dead style", "Below minimum display after plan", "New launch", "New store", "Lead time missing"]
    for kind in sorted(ex.issue.unique(), key=lambda k: order.index(k) if k in order else 99):
        grp = ex[ex.issue == kind]
        title, why, did = ISSUE_PLAIN.get(kind, (kind, "", ""))
        icon = "🔴" if (grp.severity == "High").any() else "🟡" if (grp.severity == "Medium").any() else "🔵"
        with st.container(border=True):
            st.markdown(f"**{icon} {title}** · {len(grp)} found")
            st.markdown(f"**Why it matters:** {why}  \n**What we did:** {did}")
            fig = None
            if kind in ("Stock records disagree", "Negative stock"):
                sn = P["snap"]
                rows = sn[(sn.closing_on_hand.notna() & (sn.closing_on_hand != sn.on_hand)) | (sn.on_hand < 0)]
                d = pd.DataFrame({"item": rows.location_id + " " + rows.sku_id, "Stock file": rows.on_hand,
                                  "Weekly count": rows.closing_on_hand.fillna(0)}).melt("item", var_name="record", value_name="pieces")
                fig = px.bar(d, x="item", y="pieces", color="record", barmode="group", title="The two records side by side",
                             color_discrete_sequence=["#e76f51", "#2a9d8f"])
            elif kind == "Hidden stockout":
                hs = ins.hidden_stockouts(R)
                d = hs.groupby("store_id").agg(sizes=("sku_id", "size"), lakh=("at_risk", "sum")).reset_index()
                d["lakh"] = d.lakh / 1e5
                fig = px.bar(d.sort_values("lakh"), x="lakh", y="store_id", orientation="h", text="sizes",
                             title="Sales at risk next 30 days (₹ L) · label = sizes missing", labels={"lakh": "₹ lakh", "store_id": ""},
                             color_discrete_sequence=["#e76f51"])
            elif kind == "Under-stocked store":
                s = grp.store.iloc[0]
                w = weekly[weekly.store_id.isin(stores.index)].groupby(["week", "store_id"]).days_in_stock.mean().reset_index()
                w["who"] = w.store_id.where(w.store_id == s, "other stores")
                d = w.groupby(["week", "who"]).days_in_stock.mean().reset_index()
                fig = px.line(d, x="week", y="days_in_stock", color="who", markers=True,
                              title=f"Days a week a size is on the shelf (7 = always)", labels={"days_in_stock": "days", "week": ""},
                              color_discrete_map={s: "#e76f51", "other stores": "#adb5bd"})
                fig.update_yaxes(range=[0, 7])
            elif kind == "Warehouse shortfall":
                need = g.groupby("dcat").need.sum()
                whc = pd.Series(pl.wh_start).groupby(pl.it.disp_cat).sum()
                d = pd.DataFrame({"Stores need": need, "Warehouse has": whc}).fillna(0).reset_index(names="category") \
                    .melt("category", var_name="", value_name="pieces")
                fig = px.bar(d, x="category", y="pieces", color="", barmode="group", title="Stores need vs warehouse has",
                             color_discrete_sequence=["#e76f51", "#2a9d8f"])
            elif kind == "New launch":
                stys = grp.sku.tolist()
                s = sales[sales.sku_id.map(pl.it.style_id).isin(stys) & (sales.date > P["today"] - pd.Timedelta(days=70))]
                d = s.groupby(pd.Grouper(key="date", freq="W-SAT")).qty.sum().reset_index()
                fig = px.bar(d, x="date", y="qty", title=f"{', '.join(stys)}: weekly sales since launch",
                             labels={"qty": "pieces sold", "date": ""}, color_discrete_sequence=["#2a9d8f"])
            elif kind == "New store":
                s = sales[sales.store_id.isin(grp.store)]
                d = s.groupby(pd.Grouper(key="date", freq="W-SAT")).qty.sum().reset_index()
                fig = px.bar(d, x="date", y="qty", title="Weekly sales since opening", labels={"qty": "pieces", "date": ""})
            if fig is not None:
                plot(fig, 300)
            with st.expander("Details"):
                st.dataframe(grp[["store", "sku", "detail", "action"]], hide_index=True, use_container_width=True)
    with st.container(border=True):
        st.markdown("**🟠 Old stock sitting on shelves**")
        st.markdown(f"**Why it matters:** fashion loses value the longer it sits.  \n**What we did:** "
                    f"{int(mv[mv.type == sm.T_AGE].qty.sum())} pieces go where they sell, "
                    f"{int(mv[mv.type == sm.T_PULL].qty.sum())} come back to the warehouse.")
        sn = g[g.oh > 0].copy()
        sn["band"] = pd.cut(sn.age, [-1, 30, 90, R["cfg"]["age_days"], 365, 10_000],
                            labels=["<1 mo", "1–3 mo", f"3 mo–{R['cfg']['age_days']}d", f"{R['cfg']['age_days']}d–1 yr", ">1 yr"])
        d = sn.groupby("band", observed=False).oh.sum().reset_index()
        plot(px.bar(d, x="band", y="oh", title="How long stock has been in stores", labels={"oh": "pieces", "band": ""},
                    color_discrete_sequence=["#f4a261"]), 280, legend=False)

# ---- Stores
with T[3]:
    s = st.selectbox("Store", stores.index, format_func=lambda x: f"{x} · {stores.city[x]} · tier {stores.tier[x]}")
    S = ins.store_summary(R).loc[s]
    kpi_cards([("Pieces now", f"{S.pieces_now:,}", "on shelf + on the way"), ("Coming in", f"{S.coming_in:,}", ""),
               ("Going out", f"{S.going_out:,}", ""), ("Shelves used after", f"{S.fill_after:.0%}", f"of {S.capacity:,}")])
    for r in ex[ex.store == s].itertuples():
        st.warning(f"**{ISSUE_PLAIN.get(r.issue, (r.issue,))[0]}:** {r.detail}. → {r.action}")
    gs = g.loc[s]
    inn, out = mv[mv.to_id == s], mv[mv.from_id == s]
    cat = gs.groupby("dcat").agg(now=("pos", "sum"))
    cat["after"] = cat.now + inn.groupby(inn.sku_id.map(pl.it.disp_cat)).qty.sum().reindex(cat.index).fillna(0) \
        - out.groupby(out.sku_id.map(pl.it.disp_cat)).qty.sum().reindex(cat.index).fillna(0)
    cat["minimum"] = [P["md"].get((stores.tier[s], c_), 0) for c_ in cat.index]
    d = cat.reset_index(names="category").melt("category", ["now", "after"], var_name="", value_name="pieces")
    fig = px.bar(d, x="category", y="pieces", color="", barmode="group", title="Stock by category vs minimum display",
                 color_discrete_sequence=["#adb5bd", "#2a9d8f"])
    fig.add_scatter(x=cat.index, y=cat.minimum, mode="markers", name="minimum",
                    marker=dict(symbol="line-ew", size=26, line=dict(width=3, color="#e76f51")))
    plot(fig, 320)
    cov = (gs.pos / gs.fd30).where(gs.fd30 > 0.01)
    band = pd.cut(cov, [-1, 7, 14, 30, 60, 1e9], labels=["<1 wk", "1–2 wk", "2–4 wk", "1–2 mo", "2+ mo"])
    d = band.value_counts().reindex(band.cat.categories).reset_index()
    d.columns = ["lasts", "sizes"]
    plot(px.bar(d, x="lasts", y="sizes", color="lasts", title="How long each size's stock lasts (before plan)",
                color_discrete_sequence=["#e76f51", "#f4a261", "#e9c46a", "#2a9d8f", "#577590"]), 280, legend=False)
    for title, df in (("⬇️ Coming in", inn), ("⬆️ Going out", out)):
        with st.expander(f"{title} · {len(df)} lines, {int(df.qty.sum())} pieces"):
            st.dataframe(df[["priority", "kind", "from_id", "to_id", "style_name", "size", "qty", "reason"]],
                         hide_index=True, use_container_width=True)

# ---- Approve
with T[4]:
    st.markdown("Approve by **trip** (from → to). Untick what you don't want, then download. 💰 = contains a move over ₹1 lakh.")
    routes = (mv.groupby(["type", "from_id", "to_id"])
              .agg(pieces=("qty", "sum"), value=("value_mrp", "sum"), urgent=("priority", lambda x: int((x == "High").sum())),
                   big=("approval", lambda x: (x != "").any())).reset_index().sort_values(["urgent", "value"], ascending=False))
    routes.insert(0, "ok", True)
    routes["trip"] = routes.big.map({True: "💰 ", False: ""}) + routes.from_id + " → " + routes.to_id
    routes["kind"] = routes.type.map(lambda t: TYPE_PLAIN[t][0])
    routes["value"] = routes.value.map(inr)
    ed = st.data_editor(routes[["ok", "trip", "pieces", "value", "urgent", "kind", "type", "from_id", "to_id"]], hide_index=True,
                        use_container_width=True, height=420, disabled=["trip", "pieces", "value", "urgent", "kind"],
                        column_config={"type": None, "from_id": None, "to_id": None, "ok": st.column_config.CheckboxColumn("✓", width="small"),
                                       "urgent": st.column_config.NumberColumn("urgent", width="small")})
    ok = ed[ed.ok][["type", "from_id", "to_id"]]
    approved = mv.merge(ok, on=["type", "from_id", "to_id"])
    st.success(f"Approved {len(ok)} of {len(ed)} trips · {int(approved.qty.sum()):,} pieces · {inr(approved.value_mrp.sum())}")
    cols = ["move_id", "priority", "approval", "type", "from_id", "to_id", "style_name", "size", "sku_id", "qty", "value_mrp",
            "dispatch", "arrives", "reason"]
    csv = approved[cols].assign(dispatch=approved.dispatch.dt.strftime("%Y-%m-%d"),
                                arrives=approved.arrives.dt.strftime("%Y-%m-%d")).to_csv(index=False).encode()
    st.download_button("⬇️ Download approved moves (CSV)", csv, file_name=f"approved_moves_{pl.run:%Y-%m-%d}.csv", type="primary")
    with st.expander("Every line with its reason"):
        st.dataframe(mv[["priority", "kind", "from_id", "to_id", "style_name", "size", "qty", "value_mrp", "reason"]],
                     hide_index=True, use_container_width=True, height=460)

# ---- Ask (chatbot)
with T[5]:
    key = st.session_state.get("api_key")
    ai = chat.claude_available(key)
    st.markdown(f"Ask anything about **this plan**. {'🤖 Answers by Claude AI.' if ai else '⚡ Quick answers mode.'} "
                "Questions outside the report get a polite apology.")
    if not ai:
        with st.expander("Turn on Claude AI for smarter answers"):
            k = st.text_input("Anthropic API key", type="password", help="Kept only for this browser session; never saved.")
            if st.button("Use this key") and k:
                st.session_state.api_key = k.strip()
                st.rerun()
    st.session_state.setdefault("chat", [])
    cols = st.columns(2)
    picked = None
    for i, q in enumerate(EXAMPLES):
        if cols[i % 2].button(q, key=f"ex{i}", use_container_width=True):
            picked = q
    for m in st.session_state.chat:
        with st.chat_message(m["role"], avatar="🧑" if m["role"] == "user" else "📦"):
            st.markdown(m["content"])
    q = st.chat_input("Ask about stores, items, moves, problems…") or picked
    if q:
        with st.chat_message("user", avatar="🧑"):
            st.markdown(q)
        with st.chat_message("assistant", avatar="📦"):
            with st.spinner("Looking through the report…"):
                a = (chat.ask_claude(q, st.session_state.chat, st.session_state.ctx, key) if ai else chat.quick_answer(q, R))
            st.markdown(a)
        st.session_state.chat += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    if st.session_state.chat and st.button("Clear chat"):
        st.session_state.chat = []
        st.rerun()

# ---- Feedback
with T[6]:
    runs = fb.runs()
    r = fb.ratings()
    st.markdown("#### Rate a plan")
    st.caption("Tell us how a plan worked out once the stock arrived. Your ratings tune the settings for future plans.")
    with st.form("rate", clear_on_submit=True):
        opts = runs.run_id.tolist()[::-1] if not runs.empty else [st.session_state.run_id]
        lab = {x: x for x in opts}
        if not runs.empty:
            lab = {row.run_id: f"{row.plan_date} · {row.moves} moves · {row.data[:30]} (run {row.run_at})" for row in runs.itertuples()}
        run_id = st.selectbox("Which plan?", opts, format_func=lambda x: lab.get(x, x))
        st.markdown("Overall")
        stars = st.feedback("stars")
        c1, c2, c3 = st.columns(3)
        right = c1.select_slider("Were the moves right?", [1, 2, 3, 4, 5], 4)
        easy = c2.select_slider("Easy to understand?", [1, 2, 3, 4, 5], 4)
        time_ = c3.select_slider("Saved you time?", [1, 2, 3, 4, 5], 4)
        changes = st.multiselect("What should change next time?", list(fb.SUGGESTIONS))
        comment = st.text_area("Anything else? (what went well, what went wrong)")
        name = st.text_input("Your name or role (optional)")
        if st.form_submit_button("Send feedback", type="primary"):
            if stars is None:
                st.warning("Please pick a star rating.")
            else:
                fb.add_rating(run_id, stars + 1, right, easy, time_, changes, comment, name)
                st.success("Thank you! Your feedback is saved.")
                st.rerun()
    st.markdown("#### How the app has been doing")
    if r.empty:
        st.info("No ratings yet. Be the first!")
    else:
        kpi_cards([("Average rating", f"{r.stars.mean():.1f} ★", f"{len(r)} reviews"),
                   ("Moves right", f"{r.moves_right.mean():.1f}/5", ""), ("Easy to understand", f"{r.easy.mean():.1f}/5", ""),
                   ("Saves time", f"{r.saves_time.mean():.1f}/5", "")])
        rr = r.merge(runs[["run_id", "plan_date"]], on="run_id", how="left") if not runs.empty else r.assign(plan_date=r.run_id)
        trend = rr.groupby("plan_date")[["stars", "moves_right", "easy", "saves_time"]].mean().reset_index() \
            .melt("plan_date", var_name="measure", value_name="score")
        plot(px.line(trend, x="plan_date", y="score", color="measure", markers=True, title="Ratings by plan date", range_y=[0, 5.2]), 300)
        ch = pd.Series([c.strip() for cell in r.changes.fillna("") for c in str(cell).split(";") if c.strip()])
        if len(ch):
            d = ch.value_counts().reset_index()
            d.columns = ["request", "votes"]
            plot(px.bar(d, x="votes", y="request", orientation="h", title="Most requested changes",
                        color_discrete_sequence=["#577590"]), 80 + 34 * len(d), legend=False)
        notes = r[r.comment.fillna("") != ""].tail(8)[::-1]
        for n in notes.itertuples():
            st.markdown(f"> {'★' * int(n.stars)} {n.comment}  \n> <small>{n.name or 'anonymous'} · {n.rated_at}</small>",
                        unsafe_allow_html=True)
    st.markdown("#### Suggested settings for future plans")
    cur = fb.load_settings()
    sug = fb.suggested_settings(cur)
    if sug.empty:
        st.caption("Suggestions appear here once people ask for changes in their ratings.")
    else:
        st.dataframe(sug.drop(columns="setting"), hide_index=True, use_container_width=True)
        a, b = st.columns(2)
        if a.button("✅ Use suggested settings from the next run", type="primary", use_container_width=True):
            new = dict(cur) | {row.setting: int(row.suggested) for row in sug.itertuples()}
            fb.save_settings(new)
            st.success("Saved. The next run will use these settings (you can still change them under Settings).")
        if b.button("↩️ Reset to standard settings", use_container_width=True):
            fb.reset_settings()
            st.success("Back to standard settings.")
    if not runs.empty:
        with st.expander(f"Run history ({len(runs)} runs)"):
            st.dataframe(runs.drop(columns="settings")[::-1], hide_index=True, use_container_width=True)

# ---- Method
with T[7]:
    st.markdown("#### How Stock Mover decides")
    st.graphviz_chart("""
digraph{rankdir=TB;bgcolor=transparent;node[shape=box style="rounded,filled" fontname=Helvetica fillcolor="#f1faee"];
data[label="Your data: sales · stock · deliveries · calendar · rules" fillcolor="#e9ecef"];
clean[label="1. Check & clean: fix bad records, spot problems"];
speed[label="2. Selling speed = sales ÷ days on shelf"];
season[label="3. Season: weddings & festivals lift demand"];
need[label="4. Who needs what: stock + on the way vs demand"];
plan[label="5. Plan: warehouse→store · store→store · old stock · back to WH" fillcolor="#e9f5f2"];
check[label="6. Re-check every rule" fillcolor="#fff3cd"];
out[label="Ready-to-approve list + problems explained" fillcolor="#2a9d8f" fontcolor=white];
data->clean->speed->need; season->need; need->plan->check->out;}""", use_container_width=True)
    st.markdown("""
- **Selling speed (velocity):** pieces of a size a store sells per day, counting **only days it was on the shelf**. 4 sold in 10 days in stock = 0.4/day, even if it was missing for weeks. That's how hidden stockouts are caught.
- **Cover:** how many days stock will last = stock ÷ selling speed. 10 pieces at 0.5/day = 20 days.
- **Lead time:** days for a delivery to arrive. Warehouse to store takes 3–6 days by zone; store to store takes 2–7.
- **Short on stock?** Pieces are handed out one at a time to whichever store would run out first.
- **Feedback loop:** ratings and requested changes on the Feedback tab suggest new settings; once accepted, the next run uses them.
""")
    st.markdown("#### Business rules · re-checked on every plan")
    allp = (val.status == "PASS").all()
    (st.success if allp else st.error)("All rules passed." if allp else "Some rules failed. Don't send this plan.")
    for r_ in val.itertuples():
        st.markdown(f"{'✅' if r_.status == 'PASS' else '❌'} {RULE_PLAIN.get(r_.rule, r_.rule)} "
                    f"<small>(checked {r_.checked})</small>", unsafe_allow_html=True)
    lift = pd.DataFrame(pl.season.lift).T.reset_index(names="state").melt("state", var_name="day", value_name="vs normal")
    plot(px.bar(lift, x="day", y="vs normal", color="state", barmode="group", title="Sales on special days (1.0 = normal day)")
         .add_hline(y=1, line_dash="dot"), 300)
