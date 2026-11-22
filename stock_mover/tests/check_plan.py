"""Independent check of a StockFlow plan against the raw sheets (not the engine's own rule checks).

python tests/check_plan.py [data folder] [config.json]      default: data/demo + brands/demo.json
Fails (exit 1) if any store gives more than it physically holds, a warehouse sends more than it has plus
inbound due, a protected style is given away, returns break the per-store cap, or a trip loses money.
"""
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import engine as E  # noqa: E402
import schema  # noqa: E402

data = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "data" / "demo"
cfg = schema.load_config(sys.argv[2] if len(sys.argv) > 2 else HERE / "brands" / "demo.json")
R = E.run(data, None, cfg)
mv, tr, pl = R["moves"], R["trips"], R["pl"]
T = E.read_tables(data)
snap = T["stock_snapshot"].copy()
snap["on_hand"] = pd.to_numeric(snap.on_hand)
if "stock_status" in snap:
    snap = snap[snap.stock_status.str.lower().isin(cfg["stock_status"]["sellable"])]
oh = snap.groupby(["location_id", "sku_id"]).on_hand.sum().clip(lower=0)
fails = []
out = mv[mv.from_id.isin(pl.st.index)].groupby(["from_id", "sku_id"]).qty.sum()
bad = out[out > oh.reindex(out.index).fillna(0)]
fails += [f"store gives more than it holds: {k}" for k in bad.index]
inb = T.get("inbound", T.get("warehouse_inbound"))
due = pd.Series(dtype=float)
if inb is not None:
    inb = inb.assign(qty=pd.to_numeric(inb.qty), d=pd.to_datetime(inb.expected_date))
    due = inb[inb.d <= pl.run + pd.Timedelta(days=cfg["planning"]["inbound_usable_days"])].groupby("sku_id").qty.sum()
wh = mv[mv.from_id.isin(pl.whs)].groupby("sku_id").qty.sum()
have = oh[oh.index.get_level_values(0).isin(pl.whs)].groupby(level=1).sum()
fails += [f"warehouses over-send {k}" for k, q in wh.items() if q > have.get(k, 0) + due.get(k, 0)]
prot = T.get("protected_styles", T.get("flagship_protected"))
if prot is not None:
    ps = set(zip(prot.store_id, prot.style_id))
    sty = pl.it.style_id
    fails += [f"protected style given: {f} {k}" for f, k in zip(mv.from_id, mv.sku_id) if (f, sty.get(k)) in ps]
ret = mv[mv.type.isin([E.T_PULL, E.T_OUT])].groupby("from_id").agg(q=("qty", "sum"), v=("value_mrp", "sum"))
ac = cfg["ageing"]
fails += [f"returns over cap at {s}" for s, r in ret.iterrows()
          if r.q > ac["pullback_max_units_per_store"] or r.v > ac["pullback_max_value_per_store"] + 1]
fails += [f"trip loses money: {t}" for t in tr[tr.net_benefit < 0].to_number]
print(f"{len(tr)} trips, {len(mv)} lines checked against raw sheets in {data}")
print("PASS" if not fails else "FAIL\n" + "\n".join(fails[:20]))
sys.exit(1 if fails else 0)
