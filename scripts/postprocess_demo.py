"""Post-process the recording from record_demo.py (reads docs/demo/work/marks.json and the recorded webm).
1. The long black card that hides the sandbox login is replaced by a 2.8 s non-black card
   "PayPal Sandbox buyer logs in (credentials hidden)"; the rest of the covered time is cut and the narration is re-timed.
2. Any still screen longer than 2.5 s gets a slow 1.00 -> 1.03 zoom so the video never looks stuck.
3. Muxes the narration wavs and writes docs/demo/solo_seller_order_agent_demo.mp4.
Run after record_demo.py (same env: PLAYWRIGHT_BROWSERS_PATH not needed here, only ffmpeg and Chromium for the card)."""
import glob, json, os, re, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]; W = ROOT / "docs" / "demo" / "work"; OUT = ROOT / "docs" / "demo" / "solo_seller_order_agent_demo.mp4"
M = json.loads((W / "marks.json").read_text()); vid = M["video"]
DUR = json.loads((W / "durations.json").read_text())
CARD_SEC = 2.8; FPS = 30
exe = os.environ.get("CHROMIUM_PATH") or (sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))) or [None])[-1]
def run(cmd): return subprocess.run(cmd, check=True, capture_output=True, text=True)
def ff(*a): return run(["ffmpeg", "-y", "-loglevel", "error", *a])

# 1) find the black cover (the long black segment) in the recording
bd = subprocess.run(["ffmpeg", "-hide_banner", "-i", vid, "-vf", "blackdetect=d=3:pix_th=0.05", "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
segs = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", bd)]
assert segs, "no black cover found"
b0, b1 = max(segs, key=lambda s: s[1] - s[0]); cut = (b1 - b0) - CARD_SEC
print("cover", b0, b1, "cut", round(cut, 2))

# 2) the replacement card (not black)
html = """<!doctype html><meta charset=utf-8><body style="margin:0;width:1920px;height:1080px;background:#1d4d4f;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:-apple-system,Helvetica,Arial,sans-serif;text-align:center">
<div style="font-size:64px;font-weight:700;line-height:1.3">PayPal Sandbox buyer logs in<br><span style="font-weight:500;opacity:.9">(credentials hidden)</span></div>
<div style="position:fixed;left:50%;bottom:34px;transform:translateX(-50%);padding:14px 28px;background:rgba(10,12,14,.82);font:600 34px/1.35 -apple-system,Helvetica,Arial,sans-serif;border-radius:12px">The buyer signs in to the sandbox.</div></body>"""
with sync_playwright() as p:
    br = p.chromium.launch(executable_path=exe); pg = br.new_page(viewport={"width": 1920, "height": 1080}); pg.set_content(html)
    pg.screenshot(path=str(W / "card.png")); br.close()

# 3) cut + card -> intermediate video
fc = (f"[0:v]trim=0:{b0:.3f},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[a];"
      f"[1:v]fps={FPS},format=yuv420p[c];"
      f"[0:v]trim={b1:.3f},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[b];[a][c][b]concat=n=3:v=1:a=0[v]")
ff("-i", vid, "-loop", "1", "-t", f"{CARD_SEC}", "-i", str(W / "card.png"), "-filter_complex", fc, "-map", "[v]",
   "-c:v", "libx264", "-preset", "fast", "-crf", "12", str(W / "cut.mp4"))

# 4) still stretches > 2.5 s -> slow zoom to 1.03
fz = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(W / "cut.mp4"), "-vf", "freezedetect=n=-60dB:d=2.5", "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", fz)]; durs = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", fz)]
total_cut = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(W / "cut.mp4")]).stdout)
spans = [(s, s + (durs[i] if i < len(durs) else total_cut - s)) for i, s in enumerate(starts)]
print("zoom spans", [(round(a, 1), round(b, 1)) for a, b in spans])
z = "1"
for a, b in spans:
    z = f"if(between(on/{FPS},{a:.3f},{b:.3f}),1+0.03*(on/{FPS}-{a:.3f})/{max(b - a, 0.1):.3f},{z})"
vf = (f"scale=3840:2160:flags=bicubic,zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={FPS}")
ff("-i", str(W / "cut.mp4"), "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-pix_fmt", "yuv420p", str(W / "zoomed.mp4"))

# 5) narration, re-timed: marks after the cover move earlier by `cut`; the login line starts with the card
marks = []
for k, t in M["marks"]:
    if k == "login": t = b0 + 0.1
    elif t >= b1: t -= cut
    marks.append((k, t))
total = total_cut
cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(W / "zoomed.mp4")]
for k, _ in marks: cmd += ["-i", str(W / f"{k}.wav")]
fl = "".join(f"[{i+1}:a]adelay={int(t*1000)}|{int(t*1000)}[a{i}];" for i, (k, t) in enumerate(marks))
fl += "".join(f"[a{i}]" for i in range(len(marks))) + f"amix=inputs={len(marks)}:normalize=0,apad[aout]"
cmd += ["-filter_complex", fl, "-map", "0:v", "-map", "[aout]", "-t", f"{total:.2f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT)]
run(cmd); print("wrote", OUT, round(total, 1), "s")
