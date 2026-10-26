"""Make English narration wavs for the demo. Run from the cyclopedia_upgrade repo (it owns the local Qwen3-TTS engine):
  cd /Users/dazzlar/Desktop/coding/cyclopedia_upgrade && PYTHONPATH=. .venv/bin/python /Users/dazzlar/Desktop/coding/solo-seller-order-agent/scripts/demo_tts.py
Falls back to macOS `say -v Samantha` if the local engine fails. Writes docs/demo/work/<key>.wav and durations.json."""
import json, subprocess, sys, shutil
from pathlib import Path
HERE = Path(__file__).resolve().parent; OUT = HERE.parent / "docs" / "demo" / "work"; OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE)); from demo_lines import LINES
def dur(p): return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(p)],capture_output=True,text=True).stdout)
keys = list(LINES)
try:
    from wolcheon.engine import narration
    orig = narration._settings
    narration._settings = lambda v: orig(v) | {"lang": "en"}      # English without touching the engine file
    paths = narration.synthesize([LINES[k] for k in keys], {"speaker": "Ryan", "tempo": 1.0}, OUT / "cache")
    for k, p in zip(keys, paths): shutil.copy(p, OUT / f"{k}.wav")
except BaseException as e:
    print("local TTS failed, using say:", repr(e)[:200])
    for k in keys:
        subprocess.run(["say", "-v", "Samantha", "-r", "165", "-o", str(OUT / f"{k}.aiff"), LINES[k]], check=True)
        subprocess.run(["ffmpeg","-y","-loglevel","error","-i",str(OUT/f"{k}.aiff"),str(OUT/f"{k}.wav")], check=True)
d = {k: round(dur(OUT / f"{k}.wav"), 2) for k in keys}
(OUT / "durations.json").write_text(json.dumps(d, indent=1)); print(d, "total", round(sum(d.values()), 1))
