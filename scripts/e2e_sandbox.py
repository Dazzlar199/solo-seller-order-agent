"""End-to-end run against the real PayPal sandbox: chat order -> PayPal approval (sandbox buyer) -> capture -> refund request -> seller approval.
Needs the app running (./run.sh) and SANDBOX_BUYER_EMAIL / SANDBOX_BUYER_PASSWORD in .env (fake sandbox money).
If LLM_API_KEY is set the chat is answered by the LLM; the script prints how many replies came from each path."""
import os, sys, time, glob
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config
B = "http://localhost:8000"; OUT = Path("docs/e2e"); OUT.mkdir(parents=True, exist_ok=True)
email, pw = config.env("SANDBOX_BUYER_EMAIL"), config.env("SANDBOX_BUYER_PASSWORD")
exe = os.environ.get("CHROMIUM_PATH") or (sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))) or [None])[-1]
def shot(pg, name): pg.screenshot(path=str(OUT / f"{name}.png")); print("shot", name, pg.url[:80])
with sync_playwright() as p:
    br = p.chromium.launch(executable_path=exe, headless=True)
    ctx = br.new_context(locale="en-US", viewport={"width": 1440, "height": 900}, record_video_dir=str(OUT), record_video_size={"width": 1440, "height": 900})
    pg = ctx.new_page(); pg.goto(B); pg.wait_for_timeout(1500)
    box = pg.get_by_placeholder("Describe what you want to order...")
    def send(m):
        n = pg.locator(".msg.bot").count()
        box.fill(m); box.press("Enter")
        pg.wait_for_function("n => document.querySelectorAll('.msg.bot').length > n && !document.getElementById('sendbtn').disabled", arg=n, timeout=90000)
        pg.wait_for_timeout(2500)
    for m in ["2 lavender soaps and a cedar candle", "ship to Jane Kim, 12 Oak St, Austin, TX 78701"]:
        send(m)
    shot(pg, "1_order_created")
    link = pg.locator("a[href*='paypal.com']").last; href = link.get_attribute("href"); print("pay link", href[:60])
    pg.goto(href); pg.wait_for_load_state("domcontentloaded"); pg.wait_for_timeout(5000); shot(pg, "2_paypal_login")
    pg.locator("#email").fill(email)
    if pg.locator("#btnNext").is_visible(): pg.locator("#btnNext").click(); pg.wait_for_timeout(3000)
    pg.locator("#password").fill(pw); pg.locator("#btnLogin").click()
    pg.wait_for_timeout(9000); shot(pg, "3_paypal_review")
    for _ in range(4):
        if pg.url.startswith(B): break
        import re
        b = pg.get_by_role("button", name=re.compile("Continue|Complete|Pay Now|Agree|계속|결제|구매|동의")).first
        if b.count() and b.is_visible(): print("clicking", b.inner_text()[:30]); b.click()
        pg.wait_for_timeout(8000)
    shot(pg, "3b_after_click")
    pg.wait_for_url(B + "/**", timeout=60000); pg.wait_for_timeout(4000); shot(pg, "4_back_paid")
    box = pg.get_by_placeholder("Describe what you want to order...")
    send("I want a full refund for the whole order, the candle arrived broken"); shot(pg, "5_refund_requested")
    pg.get_by_role("button", name="Approve refund").first.click(); pg.wait_for_timeout(6000); shot(pg, "6_refund_approved")
    tags = pg.locator(".tag").all_inner_texts(); print("reply sources:", {t: tags.count(t) for t in set(tags)})
    print("approval cards:", pg.locator("#approvals .card").all_inner_texts()[0][:200].replace("\n", " | "))
    ctx.close(); br.close()
print("done")
