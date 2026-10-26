"""Drive the full flow in MOCK mode and save docs/screenshot.png. Usage: python scripts/screenshot.py [base_url]"""
import sys
from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
with sync_playwright() as p:
    import os; b = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None)
    pg = b.new_page(viewport={"width": 1440, "height": 860}, device_scale_factor=1.5)
    pg.goto(base)
    def say(t):
        pg.fill("#input", t); pg.click("#sendbtn"); pg.wait_for_timeout(700)
    say("2 lavender soaps and a cedar candle")
    say("ship to Jane Kim, 12 Oak St, Austin, TX 78701")
    # buyer pays on the mock PayPal page
    with pg.context.expect_page() as np:
        pg.click("text=Pay with PayPal")
    pay = np.value; pay.click("button"); pay.wait_for_timeout(600); pay.close()
    pg.wait_for_timeout(3000)
    say("I want a refund, the candle arrived broken")
    pg.wait_for_timeout(3000)
    pg.screenshot(path="docs/screenshot.png")
    b.close()
