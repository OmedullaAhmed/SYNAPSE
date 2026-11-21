"""Run history, user ratings and suggested setting changes, kept as simple CSV/JSON files in ./feedback."""
import json
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

import stock_mover as sm

DIR = Path(__file__).resolve().parent / "feedback"
RUNS, RATINGS, SETTINGS = DIR / "runs.csv", DIR / "ratings.csv", DIR / "settings.json"

# Settings people may tune through feedback: (min, max, step) and a plain-language name
TUNABLE = {
    "cover_days": (14, 45, 3, "Days of selling each delivery covers"),
    "age_days": (60, 240, 15, "Days before stock counts as old"),
    "sell_within": (21, 75, 7, "Old stock goes only where it sells within (days)"),
    "pullback_cover": (60, 240, 30, "Bring back old stock above this many days of cover"),
}
# What a user can ask for -> which setting moves, and which way
SUGGESTIONS = {
    "Send more stock to stores (shelves ran empty)": ("cover_days", +1),
    "Send less stock (shelves got too full)": ("cover_days", -1),
    "Treat stock as old sooner": ("age_days", -1),
    "Treat stock as old later": ("age_days", +1),
    "Move old stock only to faster-selling stores": ("sell_within", -1),
    "Move old stock to more stores": ("sell_within", +1),
    "Bring back fewer pieces to the warehouse": ("pullback_cover", +1),
    "Bring back more slow pieces to the warehouse": ("pullback_cover", -1),
}


def _append(path, row):
    DIR.mkdir(exist_ok=True)
    df = pd.DataFrame([row])
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def _read(path):
    return pd.read_csv(path, dtype={"run_id": str}) if path.exists() else pd.DataFrame()


def load_settings():
    base = {k: sm.CFG[k] for k in TUNABLE}
    if SETTINGS.exists():
        try:
            base.update({k: v for k, v in json.loads(SETTINGS.read_text()).items() if k in TUNABLE})
        except (ValueError, OSError):
            pass
    return base


def save_settings(s):
    DIR.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps({k: int(s[k]) for k in TUNABLE}, indent=2))


def reset_settings():
    if SETTINGS.exists():
        SETTINGS.unlink()


def log_run(R, label, settings):
    m, val, ex = R["moves"], R["checks"], R["issues"]
    run_id = f"{R['pl'].run:%Y%m%d}-{uuid.uuid4().hex[:6]}"
    _append(RUNS, dict(run_id=run_id, run_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                       plan_date=f"{R['pl'].run:%Y-%m-%d}", data=label, moves=len(m), pieces=int(m.qty.sum()),
                       value_mrp=int(m.value_mrp.sum()), high_issues=int((ex.severity == "High").sum()),
                       rules_passed=int((val.status == "PASS").sum()), rules_total=len(val),
                       settings=json.dumps({k: int(settings.get(k, sm.CFG[k])) for k in TUNABLE})))
    return run_id


def runs():
    return _read(RUNS)


def add_rating(run_id, stars, moves_right, easy, saves_time, changes, comment, name):
    _append(RATINGS, dict(rated_at=datetime.now().strftime("%Y-%m-%d %H:%M"), run_id=run_id, stars=stars,
                          moves_right=moves_right, easy=easy, saves_time=saves_time,
                          changes="; ".join(changes), comment=(comment or "").strip(), name=(name or "").strip()))


def ratings():
    return _read(RATINGS)


def suggested_settings(current):
    """Net the votes for each tunable setting and propose one step in the winning direction."""
    r = ratings()
    rows = []
    if r.empty:
        return pd.DataFrame(columns=["setting", "what it controls", "now", "suggested", "votes for", "votes against"])
    votes = {k: [0, 0] for k in TUNABLE}
    for cell in r.changes.fillna(""):
        for label in [c.strip() for c in str(cell).split(";") if c.strip()]:
            if label in SUGGESTIONS:
                k, d = SUGGESTIONS[label]
                votes[k][0 if d > 0 else 1] += 1
    for k, (up, down) in votes.items():
        if up == down == 0:
            continue
        lo, hi, step, name = TUNABLE[k]
        net = up - down
        new = min(hi, max(lo, current[k] + (step if net > 0 else -step if net < 0 else 0)))
        rows.append({"setting": k, "what it controls": name, "now": current[k], "suggested": new,
                     "votes for": up if net >= 0 else down, "votes against": down if net >= 0 else up})
    return pd.DataFrame(rows)
