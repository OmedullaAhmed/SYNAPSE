"""Numbers and plain-language insights derived from a Stock Mover run (shared by the app and the chatbot)."""
import pandas as pd

import stock_mover as sm


def inr(v):
    v = float(v)
    return f"₹{v / 1e7:.2f} Cr" if v >= 1e7 else f"₹{v / 1e5:.1f} L" if v >= 1e5 else f"₹{v:,.0f}"


def hidden_stockouts(R):
    """Sizes that should sell but had zero days on the shelf for the last 4 weeks."""
    g, weekly, today = R["pl"].g, R["weekly"], R["P"]["today"]
    recent = weekly[weekly.week > today - pd.Timedelta(days=28)].groupby(["store_id", "sku_id"]).days.sum()
    hs = g[(g.oh == 0) & (g.fd30 >= 0.05)].copy()
    hs["days4w"] = [recent.get(k, 0) for k in hs.index]
    hs = hs[hs.days4w == 0].reset_index()
    hs["at_risk"] = hs.fd30 * 30 * hs.mrp
    return hs


def store_summary(R):
    pl, mv = R["pl"], R["moves"]
    st, g = pl.st, pl.g
    now = g.groupby(level="store_id").pos.sum()
    inn = mv[mv.to_id.isin(st.index)].groupby("to_id").qty.sum()
    out = mv[mv.from_id.isin(st.index)].groupby("from_id").qty.sum()
    hs = hidden_stockouts(R).groupby("store_id").at_risk.sum()
    df = pd.DataFrame({"city": st.city, "tier": st.tier, "capacity": st.cap, "pieces_now": now,
                       "coming_in": inn, "going_out": out, "hidden_stockout_value": hs}).fillna(0)
    df["after_plan"] = df.pieces_now + df.coming_in - df.going_out
    df["fill_after"] = df.after_plan / df.capacity
    df["area_manager"] = st.area_manager_id
    return df.astype({c: int for c in ["capacity", "pieces_now", "coming_in", "going_out", "after_plan"]})


def leadership(R):
    """Key numbers, at most five insights, and the decisions that need a person."""
    pl, mv, ex, P = R["pl"], R["moves"], R["issues"], R["P"]
    st, g, it = pl.st, pl.g, pl.it
    into = mv[mv.to_id.isin(st.index) & mv.priority.isin(["High", "Medium"]) & mv.type.isin([sm.T_WH, sm.T_S2S, sm.T_DISP])]
    hs = hidden_stockouts(R)
    old = mv[mv.type.isin([sm.T_AGE, sm.T_PULL])]
    big = mv[mv.approval != ""]
    kpis = [
        ("Sales protected", inr(into.value_mrp.sum()), f"{int(into.qty.sum()):,} pieces to stores about to run short"),
        ("Hidden lost sales found", inr(hs.at_risk.sum()), f"{len(hs)} sizes out of stock for 4+ weeks"),
        ("Old stock put to work", inr(old.value_mrp.sum()), f"{int(old.qty.sum())} pieces older than {sm.CFG['age_days']} days"),
        ("Decisions needed", str(len(big) + int((ex.issue.isin(['Warehouse shortfall', 'Dead style', 'Stock records disagree'])).sum())),
         f"{len(big)} big moves to sign off"),
    ]
    ins = []
    if len(hs):
        s = hs.groupby("store_id").at_risk.sum().sort_values(ascending=False)
        top = s.index[0]
        under = ex[(ex.issue == "Under-stocked store") & (ex.store == top)]
        ins.append(("#d1495b", f"**{top} ({st.city[top]}) is losing sales.** {int((hs.store_id == top).sum())} sizes have been "
                               f"missing from its shelves for a month, about {inr(s.iloc[0])} of sales in the next 30 days"
                    + (". Its shelves are only " + under.detail.iloc[0].split(" of capacity")[0].replace("Only ", "") + " full" if len(under) else "")
                    + ". Today's plan refills it first."))
    short = ex[ex.issue == "Warehouse shortfall"]
    for r in short.itertuples():
        ins.append(("#e8a317", f"**Not enough {r.sku} for wedding season.** {r.detail}. Stores will share what's there, "
                               "and the buying team should speed up supplier deliveries."))
    nl = ex[ex.issue == "New launch"]
    sales = R["sales"]
    for r in nl.itertuples():
        s = sales[sales.sku_id.map(it.style_id) == r.sku]
        wk = s[s.date > P["today"] - pd.Timedelta(days=28)].groupby(pd.Grouper(key="date", freq="W-SAT")).qty.sum()
        if len(wk) >= 2 and wk.iloc[0] > 0:
            name = it[it.style_id == r.sku].style_name.iloc[0]
            ins.append(("#8b1a2b", f"**{name} ({r.sku}) is a hit.** Weekly sales went from {int(wk.iloc[0])} to {int(wk.iloc[-1])} "
                                   "in four weeks. It's new, not slow, so stores are being restocked from the warehouse's big delivery."))
    bad = ex[ex.issue == "Stock records disagree"]
    if len(bad):
        b = bad.iloc[0]
        ins.append(("#6d3a7d", f"**Stock records can't be trusted everywhere.** {b.store} {b.sku}: {b.detail.split(' and the item')[0]}. "
                               "The plan ignores the suspect number, and the store should recount."))
    if len(old):
        dead = ex[ex.issue == "Dead style"]
        ins.append(("#e07020", f"**{inr(old.value_mrp.sum())} of old stock is sitting idle.** "
                               f"{int(mv[mv.type == sm.T_AGE].qty.sum())} pieces go to stores that sell them, "
                               f"{int(mv[mv.type == sm.T_PULL].qty.sum())} return to the warehouse"
                    + (f"; {', '.join(dead.sku)} has never sold and shouldn't be re-ordered." if len(dead) else ".")))
    decisions = []
    if len(big):
        decisions.append(f"Approve **{len(big)} moves worth over ₹1 lakh each** ({inr(big.value_mrp.sum())} in total). See the Approve tab.")
    for r in short.itertuples():
        decisions.append(f"Buying: speed up **{r.sku}** supply. Stores need far more than the warehouse holds.")
    for r in ex[ex.issue == "Dead style"].itertuples():
        decisions.append(f"Buying: stop re-ordering **{r.sku}**. It hasn't sold in two years.")
    for r in bad.itertuples():
        decisions.append(f"Operations: recount **{r.sku} at {r.store}**. The stock records disagree.")
    by_city = mv.assign(city=mv.to_id.map(st.city).fillna(mv.from_id.map(st.city)))
    city = pd.DataFrame({
        "Sales protected": into.assign(city=into.to_id.map(st.city)).groupby("city").value_mrp.sum(),
        "Old stock put to work": old.assign(city=old.from_id.map(st.city)).groupby("city").value_mrp.sum(),
    }).fillna(0) / 1e5
    return dict(kpis=kpis, insights=ins[:5], decisions=decisions, city=city, by_city=by_city)
