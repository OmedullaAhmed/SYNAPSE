"""Runs every check suite, each on its own fresh database, and prints one summary.

    python tests/run_all.py                         # throwaway SQLite databases
    PG_BASE=postgresql://user:pw@host:5432 python tests/run_all.py      # a new PostgreSQL database per suite

On GitHub Actions a failing suite also writes its last lines as an error annotation, so the cause shows on the run page.
"""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SUITES = ["check_plan", "enterprise_check", "api_check", "agents_check", "pilot_check", "growth_check", "app_flow_check"]


def fresh_db(base):
    import psycopg
    name = f"t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base.rstrip("/") + "/postgres", autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    return base.rstrip("/") + "/" + name


def main(only=None):
    base = os.environ.get("PG_BASE", "")
    rows, bad = [], 0
    for s in only or SUITES:
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)
        if base and s != "check_plan":
            env["DATABASE_URL"] = fresh_db(base)
        t = time.time()
        p = subprocess.run([sys.executable, str(HERE / "tests" / f"{s}.py")], cwd=HERE, env=env, capture_output=True, text=True, timeout=1800)
        out = (p.stdout + p.stderr).strip().splitlines()
        last = next((ln for ln in reversed(out) if "passed" in ln or ln.strip() in ("PASS", "FAIL")), out[-1] if out else "")
        ok = p.returncode == 0
        bad += not ok
        rows.append((s, "PASS" if ok else "FAIL", f"{time.time() - t:.0f}s", last.strip()))
        print(f"{'PASS' if ok else 'FAIL'}  {s:18} {time.time() - t:5.0f}s  {last.strip()}", flush=True)
        if not ok:
            fails = [ln for ln in out if ln.startswith("FAIL") or "Error" in ln or "Traceback" in ln][-8:] or out[-12:]
            print("\n".join("    " + ln for ln in out[-25:]))
            if os.environ.get("GITHUB_ACTIONS"):
                msg = " | ".join(x.strip()[:200] for x in fails).replace("%", "%25").replace("\n", " ")
                print(f"::error title={s} failed ({'PostgreSQL' if base else 'SQLite'})::{msg}")
    print(f"\n{len(rows) - bad}/{len(rows)} suites passed")
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::notice title=Test suites ({'PostgreSQL' if base else 'SQLite'})::" +
              " · ".join(f"{s} {st} ({last[:40]})" for s, st, _, last in rows))
    return bad


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:] or None) else 0)
