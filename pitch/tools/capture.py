"""Capture full-HD screenshots of the running Stock Mover app with Edge (headless) for the pitch video."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8502"
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)


def load_plan(page):
    page.goto(URL, wait_until="load", timeout=90_000)
    page.get_by_text("Try the sample data").click(timeout=90_000)
    page.get_by_text("Key insights").wait_for(timeout=180_000)
    page.wait_for_timeout(2500)
    page.add_style_tag(content="[data-testid='stToolbar'],[data-testid='stDecoration'],header{visibility:hidden!important}")


def tab(page, name):
    page.get_by_role("tab", name=name).click()
    page.wait_for_timeout(2500)


def scroll(page, px):
    page.mouse.move(800, 600)
    page.mouse.wheel(0, px)
    page.wait_for_timeout(1200)


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    page = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1.2, color_scheme="light")
    load_plan(page)
    shot(page, "01_leaders")
    scroll(page, 700); shot(page, "02_leaders_more")
    scroll(page, -2000)
    tab(page, "📋 Plan"); scroll(page, 900); shot(page, "03_plan_flows")
    scroll(page, -3000)
    tab(page, "🔍 Problems"); shot(page, "04_problems")
    scroll(page, 900); shot(page, "05_problems_more")
    scroll(page, -3000)
    tab(page, "🏬 Stores"); shot(page, "06_stores")
    tab(page, "✅ Approve"); shot(page, "07_approve")
    tab(page, "💬 Ask")
    box = page.get_by_placeholder("Ask about stores, items, moves, problems…")
    box.fill("Why is MZF-01 a problem?"); box.press("Enter"); page.wait_for_timeout(3000)
    box.fill("Who won the cricket world cup?"); box.press("Enter"); page.wait_for_timeout(3000)
    scroll(page, 600); shot(page, "08_chat")
    scroll(page, -3000)
    tab(page, "ℹ️ Method"); shot(page, "09_method")

    m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2.5, is_mobile=True, has_touch=True,
                   color_scheme="light")
    load_plan(m)
    shot(m, "10_mobile_leaders")
    b.close()
