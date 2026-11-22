"""Growth (marketing agent) checks: ROI arithmetic, messages carry the opt-out line, AI output is checked, lead tracker,
follow-ups due, and the demo pack builds a plan.     python tests/growth_check.py"""
import io
import os
import sys
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/growth.db"

import growth as G  # noqa: E402
import pipeline  # noqa: E402
import schema  # noqa: E402

R = []


def ok(name, cond, ev=""):
    R.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name} — {ev}")


L = dict(company="Sharma Toys", contact="Ravi Sharma", role="Owner", email="ravi@sharmatoys.example", category="toys", warehouses=2,
         stores=5, revenue_cr=20, pains=["stockouts", "excel"], city="Kolkata, Patna", next_date=date.today().isoformat())
r = G.roi(20, 5, 2)
exp = 20e7 * .03 * .5 * .35 + (20e7 * .65 * 90 / 365) * (.2 * .15 * .3 + .05 * .12) + 5 * 52 * 300
ok("ROI adds up and conservative is half", abs(r["expected"] - exp) < 1 and r["conservative"] == r["expected"] / 2,
   f"{G.money(r['conservative'])}–{G.money(r['expected'])}; payback {r['payback_months']:.1f} months")
P = G.pack(L, "Asha, StockFlow")
m = P["messages"]
ok("Email and WhatsApp carry the opt-out line; LinkedIn ≤ 300 chars", G.OPT_OUT in m["email"] and G.OPT_OUT in m["whatsapp"] and len(m["linkedin"]) <= 300,
   m["email"].splitlines()[0])
ok("Four follow-ups, the last one closes the loop", len(P["followups"]) == 4 and "won't keep writing" in P["followups"][-1]["text"], "")


class Fake:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        return NS(content=[NS(type="text", text='{"email": "Subject: Hi\\n\\nHello Ravi", "whatsapp": "Hi Ravi", '
                                               '"linkedin": "' + "x" * 400 + '", "angle": "Diwali stock-outs"}')])


P2 = G.pack(L, "Asha", notes="Opened 2 new stores in Patna", client=Fake())
ok("AI pack used, opt-out added back, LinkedIn cut to 300", P2["source"] == "ai" and P2["messages"]["email"].endswith(G.OPT_OUT)
   and len(P2["messages"]["linkedin"]) == 300 and P2["angle"] == "Diwali stock-outs", P2["angle"])
lid = G.save_lead(L, "asha")
lid2 = G.save_lead(dict(L, stores=6), "asha")
try:
    G.save_lead(dict(L, email="not-an-email"))
    bad = False
except ValueError:
    bad = True
ok("Leads: saved once per company, bad email refused", lid == lid2 and len(G.leads()) == 1 and int(G.leads().iloc[0].stores) == 6 and bad, "")
G.set_stage(lid, "Contacted", "Follow up", (date.today() - timedelta(days=1)).isoformat())
d1 = len(G.due())
G.set_stage(lid, "Not now", "Do not contact", None)
ok("Follow-ups due; 'Not now' leads drop out", d1 == 1 and len(G.due()) == 0, "")
d = Path(tempfile.mkdtemp())
zipfile.ZipFile(io.BytesIO(P["demo_zip"])).extractall(d)
out = pipeline.build_plan(d, schema.load_config(HERE / "brands" / "general_retail.json"), "demo", "t")
ok("Demo pack builds a plan with trips and passes every rule", len(out["R"]["trips"]) > 0 and (out["R"]["checks"].status == "PASS").all(),
   f"{len(out['R']['trips'])} trips, {int(out['R']['moves'].qty.sum())} pieces")
print(f"\n{sum(R)}/{len(R)} passed")
sys.exit(0 if all(R) else 1)
