"""Capture each tab of the live StockFlow AI app for the short deck."""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "tabs"
OUT.mkdir(exist_ok=True)
URL = "https://synapse-stock-mover.streamlit.app/~/+/"
TABS = [("⭐ Leaders", "1_leaders", 0), ("📋 Plan", "2_plan", 0), ("🔍 Problems", "3_problems", 0), ("🏬 Stores", "4_stores", 0),
        ("✅ Approve", "5_approve", 0), ("💬 Ask", "6_ask", 0), ("📝 Feedback", "7_feedback", 0), ("ℹ️ Method", "8_method", 0)]

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.25)
    pg.goto(URL, wait_until="load", timeout=120_000)
    pg.get_by_text("Try the sample data").click(timeout=120_000)
    pg.get_by_text("Key insights").wait_for(timeout=240_000)
    pg.wait_for_timeout(2500)
    pg.add_style_tag(content="[data-testid='stToolbar'],[data-testid='stDecoration'],header{visibility:hidden!important}")
    for name, fn, _ in TABS:
        pg.get_by_role("tab", name=name).click()
        pg.wait_for_timeout(2500)
        if name == "💬 Ask":
            box = pg.get_by_placeholder("Ask about stores, items, moves, problems…")
            box.fill("Which stores need the most stock?"); box.press("Enter"); pg.wait_for_timeout(3000)
        # bring the tab's content to the top of the view (just under the tab bar)
        tab = pg.get_by_role("tab", name=name)
        tab.scroll_into_view_if_needed()
        box_ = tab.bounding_box()
        if box_ and box_["y"] > 30:
            pg.mouse.move(720, 500)
            pg.mouse.wheel(0, box_["y"] - 20)
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / f"{fn}.png"))
        print("saved", fn)
    b.close()
