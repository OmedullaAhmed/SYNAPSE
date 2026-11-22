"""StockFlow Growth: the marketing agent for the team selling StockFlow (not for customers).

    streamlit run growth_app.py            # same database and users as the main app; admins only

Prospect -> brief, ROI estimate, tailored email / WhatsApp / LinkedIn / call script, follow-up sequence, and a demo
data pack built like the prospect's business. Pipeline -> lead tracker with stages and due follow-ups.
Nothing is sent from here: copy the text or use the links, from your own email or phone.
"""
import io
import os
import urllib.parse
from datetime import date, timedelta

import streamlit as st

import auth
import demo_factory as F
import growth as G

st.set_page_config(page_title="StockFlow Growth", page_icon="📈", layout="wide")
SECRETS = st.secrets if hasattr(st, "secrets") else None


def _secret(k):
    try:
        return SECRETS[k] if SECRETS is not None and k in SECRETS else None
    except Exception:  # noqa: BLE001
        return None


# ---- sign in (admins of this StockFlow server)
if not st.session_state.get("g_user"):
    st.title("📈 StockFlow Growth")
    st.caption("Sales tools for the StockFlow team. Sign in with an admin account of this server.")
    with st.form("g_login"):
        u = st.text_input("User name")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            ok, rec, msg = auth.attempt(u, p, SECRETS)
            if ok and rec.get("role") == "admin":
                st.session_state.g_user = u.strip()
                st.rerun()
            st.error(msg or "Only admins can use the growth tools.")
    st.stop()

USER = st.session_state.g_user
KEY = os.environ.get("ANTHROPIC_API_KEY") or _secret("ANTHROPIC_API_KEY")
st.title("📈 StockFlow Growth")
st.caption("Drafts only: you review and send. Use business contacts, keep the opt-out line, and stop when asked. "
           + ("AI personalisation is available (notes you paste are sent to Anthropic)." if KEY else "Templates only (no ANTHROPIC_API_KEY)."))
tab_p, tab_pipe, tab_due, tab_icp = st.tabs(["Prospect", "Pipeline", "Follow-ups due", "Who to target"])

with tab_p:
    d = G.leads()
    pick = st.selectbox("Prospect", ["➕ New prospect"] + (list(d.company) if len(d) else []), key="g_pick")
    cur = d[d.company == pick].iloc[0].to_dict() if pick != "➕ New prospect" and len(d) else {}
    with st.form("g_lead"):
        a, b, c = st.columns(3)
        company = a.text_input("Company", cur.get("company", ""))
        contact = b.text_input("Contact name", cur.get("contact") or "")
        role = c.text_input("Their role", cur.get("role") or "", placeholder="Owner, Head of Operations…")
        email = a.text_input("Work email", cur.get("email") or "")
        phone = b.text_input("Business phone / WhatsApp", cur.get("phone") or "")
        city = c.text_input("Cities (comma separated)", cur.get("city") or "", placeholder="Kolkata, Patna")
        cats = list(F.PRESETS)
        category = a.selectbox("Business type", cats, index=cats.index(cur.get("category")) if cur.get("category") in cats else cats.index("general"),
                               format_func=lambda k: F.PRESETS[k]["label"])
        wh = b.number_input("Warehouses", 1, 50, int(cur.get("warehouses") or 2))
        stores = c.number_input("Stores", 2, 2000, int(cur.get("stores") or 5))
        rev = a.number_input("Yearly sales (₹ crore, estimate)", 0.0, 100000.0, float(cur.get("revenue_cr") or 0.0), step=1.0,
                             help="Leave 0 if unknown: 1.5 crore per store is assumed.")
        pains = b.multiselect("Problems they mentioned", list(G.PAINS), default=[p for p in (cur.get("pains") or []) if p in G.PAINS],
                              format_func=lambda k: G.PAINS[k])
        source = c.text_input("Where you found them", cur.get("source") or "", placeholder="Trade fair, referral, directory…")
        notes = st.text_area("Notes (their About page, news, call notes)", cur.get("notes") or "", height=110,
                             help="Used to personalise the messages. With AI on, this text is sent to Anthropic.")
        sender = st.text_input("Sign messages as", st.session_state.get("g_sender", USER))
        go = st.form_submit_button("✨ Build the pitch pack", type="primary")
    with st.expander("ROI assumptions (shown to the prospect as estimates)"):
        r1, r2, r3, r4 = st.columns(4)
        kw = dict(margin=r1.slider("Gross margin", .1, .7, .35), stock_days=r2.slider("Days of stock", 20, 240, 90),
                  in_stock_gain=r3.slider("In-stock gain (points)", 1, 10, 3) / 100, old_cut=r4.slider("Cut in old stock", .05, .4, .15),
                  price_store_month=r1.number_input("Price per store per month (₹)", 0, 50000, 1500),
                  price_wh_month=r2.number_input("Price per warehouse per month (₹)", 0, 100000, 3000))
    if go:
        L = dict(company=company, contact=contact, role=role, email=email, phone=phone, city=city, category=category, warehouses=int(wh),
                 stores=int(stores), revenue_cr=rev or None, pains=pains, source=source, notes=notes,
                 stage=cur.get("stage") or "New", next_step=cur.get("next_step") or "First contact",
                 next_date=cur.get("next_date") or date.today().isoformat())
        try:
            lid = G.save_lead(L, USER)
            st.session_state.g_sender = sender
            with st.spinner("Writing the pack…"):
                P = G.pack(L, sender, notes, KEY, **kw)
            G.save_assets(lid, {k: v for k, v in P.items() if k not in ("demo_zip", "roi")})
            st.session_state.g_pack = (company, P)
        except ValueError as e:
            st.error(str(e))
    if st.session_state.get("g_pack"):
        name, P = st.session_state.g_pack
        st.subheader(f"Pitch pack: {name}")
        st.caption("Personalised by AI from your notes." if P["source"] == "ai" else "From templates.")
        if P.get("angle"):
            st.info(P["angle"])
        st.text(P["brief"])
        m = P["messages"]
        t1, t2, t3, t4 = st.tabs(["Email", "WhatsApp", "LinkedIn", "Call script"])
        with t1:
            em = st.text_area("Email", m["email"], height=320, key="g_em")
            subj, _, body = em.partition("\n\n")
            to = (d[d.company == name].email.iloc[0] if len(d) and (d.company == name).any() else "") or ""
            st.link_button("Open in my email", f"mailto:{to}?subject={urllib.parse.quote(subj.replace('Subject:', '').strip())}"
                                               f"&body={urllib.parse.quote(body)}")
        with t2:
            wa = st.text_area("WhatsApp", m["whatsapp"], height=160, key="g_wa")
            ph = "".join(ch for ch in str(phone or "") if ch.isdigit())
            if ph:
                st.link_button("Open in WhatsApp", f"https://wa.me/{('91' + ph) if len(ph) == 10 else ph}?text={urllib.parse.quote(wa)}")
        with t3:
            st.text_area("LinkedIn note (300 characters)", m["linkedin"], height=110, key="g_li")
        with t4:
            st.text(m["call"])
        st.markdown("**Follow-up sequence** (stop as soon as they reply or say no)")
        for f in P["followups"]:
            with st.expander(f"Day {f['day']} · {f['date']}"):
                st.text(f["text"])
        c1, c2 = st.columns(2)
        c1.download_button("⬇️ Demo data built like them (.zip)", P["demo_zip"], f"{name.replace(' ', '_')}_demo.zip", type="primary",
                           help="Upload it in StockFlow (Plan with your own data) before the demo. Invented data.")
        c2.download_button("⬇️ Pitch pack (text)", "\n\n".join([P["brief"], m["email"], m["whatsapp"], m["linkedin"], m["call"]]
                                                               + [f"Day {f['day']}:\n{f['text']}" for f in P["followups"]]),
                           f"{name.replace(' ', '_')}_pitch.txt")

with tab_pipe:
    d = G.leads()
    if d.empty:
        st.info("No prospects yet. Add one under Prospect.")
    else:
        k = d.stage.value_counts()
        cols = st.columns(len(G.STAGES))
        for i, s_ in enumerate(G.STAGES):
            cols[i].metric(s_, int(k.get(s_, 0)))
        view = d[["id", "company", "contact", "category", "warehouses", "stores", "stage", "next_step", "next_date", "owner", "updated_at"]].copy()
        ed = st.data_editor(view, hide_index=True, width="stretch", key="g_ed", disabled=["id", "company", "contact", "category", "warehouses",
                                                                                          "stores", "owner", "updated_at"],
                            column_config={"id": None, "stage": st.column_config.SelectboxColumn(options=G.STAGES),
                                           "next_date": st.column_config.TextColumn(help="YYYY-MM-DD")})
        if st.button("💾 Save pipeline changes"):
            n = 0
            for (_, a_), (_, b_) in zip(view.iterrows(), ed.iterrows()):
                if (a_.stage, a_.next_step, a_.next_date) != (b_.stage, b_.next_step, b_.next_date):
                    G.set_stage(a_.id, b_.stage, str(b_.next_step or ""), str(b_.next_date or "")[:10] or None, USER)
                    n += 1
            st.success(f"Saved {n} change(s).")
        buf = io.StringIO()
        d.drop(columns=["assets"], errors="ignore").to_csv(buf, index=False)
        st.download_button("⬇️ Pipeline (CSV)", buf.getvalue(), "pipeline.csv")

with tab_due:
    dd = G.due()
    if dd.empty:
        st.success("No follow-ups due today.")
    for r in dd.itertuples():
        seq = (r.assets or {}).get("followups", []) if isinstance(r.assets, dict) else []
        nxt = next((f for f in seq if f["date"] >= str(date.today())), seq[-1] if seq else None)
        with st.expander(f"{r.company} · {r.stage} · due {r.next_date} · {r.next_step or ''}"):
            if nxt:
                st.text(nxt["text"])
            a_, b_ = st.columns(2)
            if a_.button("Done: next in 4 days", key=f"g_d_{r.id}"):
                G.set_stage(r.id, "Contacted" if r.stage == "New" else r.stage, "Follow up", (date.today() + timedelta(days=4)).isoformat(), USER)
                st.rerun()
            if b_.button("They said no: stop", key=f"g_n_{r.id}"):
                G.set_stage(r.id, "Not now", "Do not contact", None, USER)
                st.rerun()

with tab_icp:
    st.markdown("""
**Best fit (start here)**
- 2–10 warehouses or depots and 5–200 stores, one owner or a small management team.
- Toys, apparel and footwear, electronics and mobiles, pharmacy chains, grocery chains, auto spares.
- Already exports sales and stock from a POS or ERP (Tally, Busy, Marg, Ginesys, Logic, SAP Business One, Excel).
- Signs: weekly transfer meetings, WhatsApp groups for stock, frequent "out of stock" in one store while another has it.

**Where to find them**
- Trade associations and fairs (toy, textile, pharma distributor and FMCG distributor associations), franchise expos.
- POS / ERP resellers and Tally partners (they meet these businesses every week: offer them a referral fee).
- Chartered accountants and auditors of mid-size retailers.
- Google Maps and marketplace seller lists for chains with many branches in one region.

**First offer:** a free plan on their own data ("send one month of sales and today's stock, any export"), then a 30-day paid pilot
for 1 warehouse and 5–10 stores with a clear success measure (in-stock %, old stock, hours saved).

**Rules:** business contacts only; one message, then at most four follow-ups; stop at the first 'no'; never buy contact lists.
""")
