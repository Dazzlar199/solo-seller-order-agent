"""Record the demo video against the real PayPal sandbox (app must be running: ./run.sh, MOCK_PAYPAL=0).
Needs SANDBOX_BUYER_EMAIL / SANDBOX_BUYER_PASSWORD in .env. Credentials are covered by a black card in the video. The chat is answered by the LLM when LLM_API_KEY is set.
Steps: python scripts/demo_tts.py (see its docstring) -> python scripts/record_demo.py
Note: if Playwright's bundled ffmpeg is too old for record_video (no webm output), point PLAYWRIGHT_BROWSERS_PATH at a folder whose
ffmpeg-<rev>/ffmpeg-mac is a wrapper around a current ffmpeg and which symlinks the chromium dirs.
Output: docs/demo/solo_seller_order_agent_demo.mp4 (work files in docs/demo/work, git-ignored).
Then run scripts/postprocess_demo.py: it swaps the long black login cover for a short card and adds a slow zoom on still screens."""
import glob, json, os, re, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from app import config
from demo_lines import LINES
W = ROOT / "docs" / "demo" / "work"; OUTMP4 = ROOT / "docs" / "demo" / "solo_seller_order_agent_demo.mp4"
DUR = json.loads((W / "durations.json").read_text())
B = "http://localhost:8000"
email, pw = config.env("SANDBOX_BUYER_EMAIL"), config.env("SANDBOX_BUYER_PASSWORD")
exe = os.environ.get("CHROMIUM_PATH") or (sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))) or [None])[-1]

INIT = r"""
(() => {
  const css = `#__sub{position:fixed;left:50%;bottom:34px;transform:translateX(-50%);max-width:1560px;width:max-content;padding:14px 28px;background:rgba(10,12,14,.82);color:#fff;font:600 34px/1.35 -apple-system,Helvetica,Arial,sans-serif;text-align:center;border-radius:12px;z-index:2147483647;display:none;pointer-events:none}
  #__mask{position:fixed;inset:0;background:#000;color:#fff;z-index:2147483646;display:none;align-items:center;justify-content:center;font:600 54px/1.4 -apple-system,Helvetica,Arial,sans-serif;text-align:center;pointer-events:none}`;
  const app = location.hostname === 'localhost' ? `main{height:calc(100vh - 66px - 150px)!important}` : '';
  const render = () => {
    if (!document.documentElement) return;
    let st = document.getElementById('__st'); if (!st) { st = document.createElement('style'); st.id = '__st'; document.documentElement.appendChild(st); }
    st.textContent = css + app;
    let m = document.getElementById('__mask'); if (!m) { m = document.createElement('div'); m.id = '__mask'; document.documentElement.appendChild(m); }
    let s = document.getElementById('__sub'); if (!s) { s = document.createElement('div'); s.id = '__sub'; document.documentElement.appendChild(s); }
    const t = sessionStorage.getItem('__sub') || '', k = sessionStorage.getItem('__mask') || '';
    s.textContent = t; s.style.display = t ? 'block' : 'none';
    m.innerHTML = k ? k : ''; m.style.display = k ? 'flex' : 'none';
  };
  window.__render = render;
  document.addEventListener('DOMContentLoaded', render); render();
  setInterval(render, 200);
})();
"""
def card(body, extra=""):
    return f"""<!doctype html><html><head><meta charset=utf-8><style>
body{{margin:0;width:1920px;height:1080px;background:#f4f1ec;color:#1f2328;font-family:-apple-system,Helvetica,Arial,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center}}
h1{{font-size:92px;margin:0 0 18px;color:#1d4d4f;letter-spacing:-1px}} .tag{{font-size:40px;color:#5a5340;margin-bottom:56px}}
.row{{display:flex;gap:28px;align-items:stretch}} .box{{background:#fff;border:3px solid #1d4d4f;border-radius:18px;padding:26px 34px;font-size:34px;min-width:330px}}
.box small{{display:block;font-size:26px;color:#5a5340;margin-top:12px;line-height:1.4}} .arrow{{align-self:center;font-size:60px;color:#1d4d4f}}
.human{{border-color:#b3261e}} .human b{{color:#b3261e}} .pp{{border-color:#2c4a9a;background:#eef2fb}}
.pb{{font-size:46px;line-height:1.7;color:#1f2328}} .pb span{{color:#b3261e;font-weight:700}} {extra}</style></head><body>{body}</body></html>"""
CARD_PROBLEM = card("""<h1>Solo Seller Order Agent</h1><div class=tag>For the one-person shop</div>
<div class=pb>Orders arrive in chat.<br>Payment links are built by hand.<br>Paid or not? Checked by hand.<br>Refund requests come at midnight.</div>""")
CARD_PROMISE = card("""<h1>Solo Seller Order Agent</h1><div class=tag>An AI agent drafts. A human approves money movement.</div>
<div class=pb>The agent can take the order and request a refund.<br><span>Only the seller can approve a refund.</span></div>""")
CARD_ARCH = card("""<div style="font-size:56px;font-weight:700;color:#1d4d4f;margin-bottom:36px">Architecture</div>
<div class=row><div class=box>Customer chat</div><div class=arrow>&rarr;</div>
<div class=box><b>Agent</b> (LLM or rules)<small>tools: list_products, create_order,<br>get_order_status, request_refund,<br>request_cancel<br><b>No refund execution tool</b></small></div><div class=arrow>&rarr;</div>
<div class="box pp"><b>PayPal Agent Toolkit</b><small>create_order<br>get_order_details</small></div></div>
<div style="height:34px"></div>
<div class=row><div class="box human"><b>Seller approval queue</b><small>agent requests refunds here<br>human click only</small></div><div class=arrow>&rarr;</div>
<div class="box pp"><b>PayPal REST v2</b><small>capture after buyer approval<br>refund after seller approval</small></div></div>
<div style="margin-top:50px;font-size:40px;color:#1d4d4f;font-weight:600">Open source, MIT licensed</div>
<div style="margin-top:10px;font-size:28px;color:#5a5340">Runs on the PayPal Sandbox. Claude via function calling.</div>""")

marks = []; sub_queue = []; cur_end = 0.0
def now(): return time.time() - T0
def tick(pg):
    while sub_queue and sub_queue[0][0] <= now():
        _, text = sub_queue.pop(0)
        try: pg.evaluate("t => { sessionStorage.setItem('__sub', t); window.__render && window.__render(); }", text)
        except Exception: pass
def sleep(pg, sec):
    end = time.time() + sec
    while time.time() < end: tick(pg); time.sleep(0.05)
def sleep_until_narr_done(pg):
    while now() < cur_end: tick(pg); time.sleep(0.05)
def say(pg, key):
    global cur_end
    sleep_until_narr_done(pg)
    start = now(); marks.append((key, start))
    text = LINES[key]; sents = re.split(r"(?<=[.!?])\s+", text); total = sum(len(s) for s in sents); t = start
    for s in sents:
        sub_queue.append((t, s)); t += DUR[key] * len(s) / total
    cur_end = start + DUR[key] + 0.5; tick(pg)
def wait_for(pg, cond, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        tick(pg)
        try:
            if cond(): return True
        except Exception: pass
        time.sleep(0.1)
    return False
def zoom(pg, sel, s=1.6, dur=0.9):
    pg.evaluate("""([sel, s, dur]) => {
      let z = document.getElementById('__z');
      if (!z) { z = document.createElement('div'); z.id = '__z'; z.style.cssText = 'transform-origin:0 0;width:100vw;height:100vh;overflow:visible'; const h = document.querySelector('header'), m = document.querySelector('main'); document.body.insertBefore(z, h); z.appendChild(h); z.appendChild(m); document.body.style.overflow = 'hidden'; }
      z.style.transition = `transform ${dur}s ease`;
      if (!sel) { z.style.transform = 'none'; return; }
      const r = document.querySelector(sel).getBoundingClientRect(); const W = innerWidth, H = innerHeight;
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      let tx = W / 2 - s * cx, ty = (H - 150) / 2 - s * cy; tx = Math.min(0, Math.max(W - s * W, tx)); ty = Math.min(0, Math.max(H - s * H, ty));
      z.style.transform = `translate(${tx}px,${ty}px) scale(${s})`; }""", [sel, s, dur])
def type_chat(pg, text, wait_reply=True):
    n = pg.locator(".msg.bot").count()
    box = pg.get_by_placeholder("Describe what you want to order..."); box.click(); pg.keyboard.type(text, delay=55); sleep(pg, 0.6); box.press("Enter")
    if wait_reply:  # the LLM answers in a few seconds; wait for the reply and the re-enabled Send button
        wait_for(pg, lambda: pg.locator(".msg.bot").count() > n and not pg.locator("#sendbtn").is_disabled(), 90)
        sleep(pg, 1.2)

with sync_playwright() as p:
    br = p.chromium.launch(executable_path=exe, headless=True)
    ctx = br.new_context(locale="en-US", viewport={"width": 1920, "height": 1080}, record_video_dir=str(W / "rec"), record_video_size={"width": 1920, "height": 1080})
    ctx.add_init_script(INIT)
    T0 = time.time(); pg = ctx.new_page()
    # 1) problem
    pg.set_content(CARD_PROBLEM); say(pg, "problem"); sleep_until_narr_done(pg)
    pg.set_content(CARD_PROMISE); say(pg, "promise"); sleep_until_narr_done(pg)
    # 2) app + order
    pg.goto(B); sleep(pg, 1.0); say(pg, "app"); sleep(pg, 2.5); zoom(pg, ".badges", 2.4); sleep(pg, 4.5); zoom(pg, None); sleep_until_narr_done(pg)
    say(pg, "order"); sleep(pg, 0.4)
    type_chat(pg, "2 lavender soaps and a cedar candle"); sleep(pg, 1.5)
    type_chat(pg, "ship to Jane Kim, 12 Oak St, Austin, TX 78701"); sleep(pg, 1.5)
    say(pg, "created"); sleep(pg, 2.0); zoom(pg, "#orders .card", 1.5); sleep_until_narr_done(pg); zoom(pg, None); sleep(pg, 0.9)
    href = pg.locator("a[href*='paypal.com']").last.get_attribute("href")
    # 3) PayPal
    say(pg, "checkout"); pg.goto(href); pg.wait_for_load_state("domcontentloaded"); sleep(pg, 4.0)
    sleep_until_narr_done(pg)
    MASK = "<div>Buyer signs in to PayPal Sandbox<div style='font-size:34px;font-weight:400;opacity:.8;margin-top:14px'>Credentials hidden in this recording</div></div>"
    pg.evaluate("m => { sessionStorage.setItem('__mask', m); window.__render(); }", MASK)
    say(pg, "login"); sleep(pg, 0.6)
    pg.locator("#email").fill(email)
    if pg.locator("#btnNext").is_visible(): pg.locator("#btnNext").click(); sleep(pg, 3.0)
    pg.locator("#password").fill(pw); pg.locator("#btnLogin").click()
    sleep(pg, 8.0)
    def mask_safe(): return email.lower() not in pg.inner_text("body").lower() and pw not in pg.inner_text("body")
    # keep covered until the page no longer shows the login / buyer email
    wait_for(pg, lambda: pg.locator("#password").count() == 0 and mask_safe(), 40)
    print("review url", pg.url[:90]); sleep_until_narr_done(pg)
    pg.evaluate("() => { sessionStorage.removeItem('__mask'); window.__render(); }")
    pg.screenshot(path=str(W / "dbg_review.png"))
    say(pg, "review"); sleep(pg, 1.0)
    for _ in range(5):
        if pg.url.startswith(B): break
        b = pg.get_by_role("button", name=re.compile("Continue|Complete|Pay Now|Agree|계속|결제|구매|동의")).first
        if b.count() and b.is_visible():
            print("clicking", b.inner_text()[:30]); b.click()
        wait_for(pg, lambda: pg.url.startswith(B), 4.0)
    pg.wait_for_url(B + "/**", timeout=60000); sleep(pg, 2.5)
    say(pg, "paid"); sleep(pg, 0.5); zoom(pg, "#orders .card", 1.5); sleep_until_narr_done(pg); sleep(pg, 0.8); zoom(pg, None); sleep(pg, 0.9)
    # 4) refund
    say(pg, "refund"); sleep(pg, 0.3)
    type_chat(pg, "I want a full refund for the whole order, the candle arrived broken"); sleep(pg, 1.0)
    say(pg, "queue"); zoom(pg, "#approvals .card", 1.5); sleep_until_narr_done(pg); sleep(pg, 0.5); zoom(pg, None); sleep(pg, 0.9)
    say(pg, "approve"); sleep(pg, 2.0)
    pg.get_by_role("button", name="Approve refund").first.click(); sleep(pg, 2.0)
    say(pg, "done"); sleep(pg, 3.5); zoom(pg, "#approvals .card", 1.5); sleep(pg, 4.0); zoom(pg, "#orders .card", 1.5)
    pg.screenshot(path=str(W / "dbg_done.png")); sleep_until_narr_done(pg); sleep(pg, 0.6); zoom(pg, None); sleep(pg, 0.5)
    # 5) architecture
    pg.set_content(CARD_ARCH); say(pg, "arch"); sleep_until_narr_done(pg); sleep(pg, 1.0)
    total = now()
    vid = pg.video.path(); ctx.close(); br.close()
print("recorded", round(total, 1), "s", vid)
json.dump({"marks": marks, "total": total, "video": str(vid)}, open(W / "marks.json", "w"))
# mux: webm -> mp4 with narration placed at the mark times
OFFSET = float(os.environ.get("AUDIO_OFFSET", "0.0"))
cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(vid)]
for k, _ in marks: cmd += ["-i", str(W / f"{k}.wav")]
fl = "".join(f"[{i+1}:a]adelay={int((t+OFFSET)*1000)}|{int((t+OFFSET)*1000)}[a{i}];" for i, (k, t) in enumerate(marks))
fl += "".join(f"[a{i}]" for i in range(len(marks))) + f"amix=inputs={len(marks)}:normalize=0,apad[aout]"
cmd += ["-filter_complex", fl, "-map", "0:v", "-map", "[aout]", "-t", f"{total:.2f}", "-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUTMP4)]
subprocess.run(cmd, check=True); print("wrote", OUTMP4)
