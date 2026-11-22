"""Purchase suggestions: what to buy because no stock exists anywhere in the chain to move.

After every planned transfer, each store still short of its target (selling speed x (lead time + cover days)) adds to the
shortfall of that SKU. Spare warehouse stock left after the plan and supplier deliveries already due are taken off; the
rest is rounded up to the supplier's pack size and minimum order (optional item columns: vendor, pack_size, moq).
End-of-season items and outlets are never bought for.
"""
import math

import numpy as np
import pandas as pd


def suggestions(R):
    pl, mv, cfg = R["pl"], R["moves"], R["cfg"]
    g, it = pl.g, pl.it
    fin = g.pos.copy()
    inc = mv[mv.to_id.isin(pl.st.index)].groupby(["to_id", "sku_id"]).qty.sum()
    for k, q in inc.items():
        fin[k] += q
    out_ = mv[mv.from_id.isin(pl.st.index)].groupby(["from_id", "sku_id"]).qty.sum()
    for k, q in out_.items():
        fin[k] -= q
    x = g.assign(final=fin).reset_index()
    x = x[~x.store_id.isin(pl.outlets) & ~x.eoss & (x.fd30 > 0)]
    x["short"] = np.maximum(0, np.ceil(x.target - x.final - 0.5))
    short = x[x.short > 0].groupby("sku_id").agg(short=("short", "sum"), stores=("store_id", "nunique"), sells_day=("fd30", "sum"))
    if short.empty:
        return pd.DataFrame(columns=["sku_id", "name", "category", "vendor", "stores_short", "units_short", "warehouse_spare",
                                     "on_order_later", "suggest_buy", "cost_value", "sells_per_day"])
    spare = pd.Series({k: sum(q for _, q in v) for k, v in pl.supply.items() if v}, dtype=float)
    spare = spare.groupby(level=1).sum() if len(spare) else pd.Series(dtype=float)       # (warehouse, sku) -> sku
    later = pd.Series(dtype=float)
    inb = R["P"].get("inbound")
    if inb is not None and len(inb) and "expected_date" in inb:
        usable = pl.run + pd.Timedelta(days=cfg["planning"]["inbound_usable_days"])
        later = inb[inb.expected_date > usable].groupby("sku_id").qty.sum()
    d = short.reset_index()
    d["warehouse_spare"] = d.sku_id.map(spare).fillna(0)
    d["on_order_later"] = d.sku_id.map(later).fillna(0)
    need = (d.short - d.warehouse_spare - d.on_order_later).clip(lower=0)
    pack = d.sku_id.map(pd.to_numeric(it.get("pack_size", pd.Series(dtype=float)), errors="coerce")).fillna(1).clip(lower=1)
    moq = d.sku_id.map(pd.to_numeric(it.get("moq", pd.Series(dtype=float)), errors="coerce")).fillna(0)
    d["suggest_buy"] = [0 if n <= 0 else max(int(math.ceil(n / p) * p), int(m)) for n, p, m in zip(need, pack, moq)]
    d = d[d.suggest_buy > 0]
    d["name"] = d.sku_id.map(it.style_name)
    d["category"] = d.sku_id.map(it.category)
    d["vendor"] = d.sku_id.map(it["vendor"]).fillna("") if "vendor" in it else ""
    d["cost_value"] = d.suggest_buy * d.sku_id.map(it.cost)
    d = d.rename(columns={"short": "units_short", "stores": "stores_short", "sells_day": "sells_per_day"})
    cols = ["sku_id", "name", "category", "vendor", "stores_short", "units_short", "warehouse_spare", "on_order_later", "suggest_buy",
            "cost_value", "sells_per_day"]
    return d[cols].sort_values("cost_value", ascending=False).reset_index(drop=True)
