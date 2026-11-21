"""Re-capture the chatbot screen so both a real answer and the off-topic apology are visible."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent / "shots"

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    page = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1.2, color_scheme="light")
    page.goto("http://localhost:8502", wait_until="load", timeout=90_000)
    page.get_by_text("Try the sample data").click(timeout=90_000)
    page.get_by_text("Key insights").wait_for(timeout=180_000)
    page.wait_for_timeout(2000)
    page.add_style_tag(content="[data-testid='stToolbar'],[data-testid='stDecoration'],header{visibility:hidden!important}")
    page.get_by_role("tab", name="💬 Ask").click()
    page.wait_for_timeout(1500)
    box = page.get_by_placeholder("Ask about stores, items, moves, problems…")
    for q in ["What needs approval?", "Who won the cricket world cup?"]:
        box.fill(q)
        box.press("Enter")
        page.wait_for_timeout(3000)
    page.get_by_text("Sorry, I can only answer").last.scroll_into_view_if_needed()
    page.mouse.move(800, 500)
    page.mouse.wheel(0, 250)
    page.wait_for_timeout(1000)
    page.screenshot(path=str(OUT / "08_chat.png"))
    b.close()
print("saved 08_chat")
