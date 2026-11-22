"""Concurrent-user test: N approvers (approver0..N-1, password in PASSWORD) sign in, reopen the latest plan and open
Approve, Operations, Exceptions and Inventory. Needs Playwright + Chrome.  URL=http://host:port USERS=10 python tests/load_test.py"""
import asyncio, time, json, statistics
from playwright.async_api import async_playwright
import os
N = int(os.environ.get("USERS", "10"))
URL = os.environ.get("URL", "http://localhost:8599")
async def user(p, i, res):
    b = await p.chromium.launch(**({"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}))
    pg = await b.new_page(viewport={"width": 1300, "height": 900})
    t0 = time.time(); r = {"user": i}
    try:
        await pg.goto(URL, timeout=120000)
        await pg.get_by_label("User name").fill(f"approver{i}", timeout=120000)
        await pg.locator("input[type=password]").first.fill(os.environ.get("PASSWORD", "Strongpass123"))
        await pg.get_by_role("button", name="Sign in", exact=True).click()
        await pg.get_by_role("button", name="Reopen").first.click(timeout=120000)
        t1 = time.time()
        await pg.get_by_role("tab", name="Operations").wait_for(timeout=240000)
        r["plan_open_s"] = round(time.time() - t1, 1)
        for tab in ("Approve", "Operations", "Exceptions", "Inventory"):
            t2 = time.time()
            await pg.get_by_role("tab", name=tab).click()
            await pg.wait_for_timeout(300)
            await pg.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"] [data-testid=\"stStatusWidgetRunningIcon\"]')", timeout=120000)
            r[f"{tab}_s"] = round(time.time() - t2, 1)
        exc = await pg.locator("[data-testid='stException']").count()
        r["exceptions_on_page"] = exc
        r["total_s"] = round(time.time() - t0, 1)
        r["ok"] = exc == 0
    except Exception as e:
        r["ok"] = False; r["error"] = str(e)[:200]
        await pg.screenshot(path=f"/tmp/load_fail_{i}.png")
    res.append(r); await b.close()
async def main():
    res = []
    async with async_playwright() as p:
        t = time.time()
        await asyncio.gather(*[user(p, i, res) for i in range(N)])
        wall = time.time() - t
    ok = [r for r in res if r.get("ok")]
    print(json.dumps(sorted(res, key=lambda r: r["user"]), indent=0)[:3000])
    if ok:
        print(f"\n{len(ok)}/{N} users OK in {wall:.0f}s wall; plan open median {statistics.median(r['plan_open_s'] for r in ok)}s, "
              f"max {max(r['plan_open_s'] for r in ok)}s; total per user median {statistics.median(r['total_s'] for r in ok)}s")
asyncio.run(main())
