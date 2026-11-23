"""Web-app flows in a headless browser (Streamlit AppTest): owner sign-in, demo plan, approve and release, agents,
AI cap, pilot set-up, full export, and the guest demo (no live data, no agents).

    python tests/app_flow_check.py          # throwaway SQLite database unless DATABASE_URL is set
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/app_flow.db"

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(HERE / "app.py")
RESULTS = []


def ok(name, cond, ev=""):
    RESULTS.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name} — {str(ev)[:250]}", flush=True)


def wait_plan(at, limit=300):
    t = time.time()
    while "R" not in at.session_state and time.time() - t < limit:
        time.sleep(1.5)
        at.run()
    return time.time() - t


def errs(at):
    return [e.value for e in at.exception][:2]


at = AppTest.from_file(APP, default_timeout=300)
at.run()
ti = at.text_input
ti[0].input("owner")
ti[1].input("Strongpass123")
if len(ti) >= 3 and ti[2].label.lower().startswith("repeat"):
    ti[2].input("Strongpass123")
at.button[0].click().run()
[b for b in at.button if "apparel demo" in b.label.lower()][0].click().run()
s = wait_plan(at)
tabs = [x.label for x in at.tabs]
ok("Owner signs in and builds the apparel demo plan", "R" in at.session_state and not errs(at), f"{s:.0f}s; {len(tabs)} tabs")
ok("Agents, Operations, Integrations and Admin tabs are there for the owner", {"Agents", "Operations", "Integrations", "Admin"} <= set(tabs), tabs)
b = [b for b in at.button if b.label.startswith("Approve all pending")]
if b:
    b[0].click().run()
rel = [b for b in at.button if b.label.startswith("📤 Release") and "approved trips" in b.label]
if rel:
    rel[0].click().run()
ok("Approve all and release from the regional queue", rel and any("Released" in x.value for x in at.success) and not errs(at),
   [x.value[:80] for x in at.success][:2])
[b for b in at.button if b.label == "Run morning brief"][0].click().run()
ok("Morning brief agent runs and its drafts show", any(e.label.startswith("Morning brief") for e in at.expander) and not errs(at), "")
cap = [x for x in at.number_input if x.label == "Monthly cap (₹)"]
ok("AI spending cap shows ₹500", cap and cap[0].value == 500, "")
pst = [m for m in at.multiselect if m.label == "Pilot stores"]
cst = [m for m in at.multiselect if m.label.startswith("Comparison stores")]
if pst and cst:
    opts = pst[0].options
    pst[0].set_value(opts[:3])
    cst[0].set_value(opts[3:6])
    [x for x in at.number_input if x.label.startswith("Planner hours a week")][0].set_value(20.0)
    [b for b in at.button if b.label == "Save pilot set-up"][0].click().run()
import pilot  # noqa: E402

sc = pilot.scorecard()
ok("Pilot set-up saves and the scorecard shows six marks", sc["of"] == 6 and not errs(at), pilot.verdict(sc))
[b for b in at.button if b.label == "📦 Prepare full export"][0].click().run()
ok("Admin prepares the full data export", any(x.label.startswith("⬇️ Download export") for x in at.get("download_button")) and not errs(at), "")

g = AppTest.from_file(APP, default_timeout=300)
g.run()
gb = [b for b in g.button if "without signing" in b.label]
if gb:
    gb[0].click().run()
    wait_plan(g, 120)
gt = [x.label for x in g.tabs]
ok("Guest demo works without live data or agents", gb and "R" in g.session_state and "Agents" not in gt and "Admin" not in gt and not errs(g), gt)
print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
