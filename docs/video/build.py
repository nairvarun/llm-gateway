"""Render slides, synthesize narration, and assemble the explainer video.

Usage: python build.py [render|tts|video|all] [--only N] [--voice NAME]

--voice is a Kokoro voice (default am_michael; e.g. af_heart, bf_emma, bm_george) or "piper".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

from slides import CHAPTERS, SLIDES

W = Path(__file__).parent
OUT = W / "out"
FRAMES = OUT / "frames"
AUDIO = OUT / "audio"
SEGS = OUT / "segs"
CHROME = os.environ.get("CHROME_PATH")  # optional; otherwise Playwright's own Chromium
VOICES = W / "voice"  # model files, git-ignored (see README.md)
STEP_PAD = 0.45  # silence after each step
SLIDE_PAD = 0.9  # extra silence at the end of each slide

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1920px; height: 1080px; }
body { background: #F6F5F1; color: #1C1F26; font-family: Inter, sans-serif; position: relative; overflow: hidden; }
.header { position: absolute; left: 110px; top: 70px; right: 110px; }
.chapter { color: #2559D9; font-size: 24px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; }
.title { font-size: 60px; font-weight: 750; letter-spacing: -1px; margin-top: 10px; }
.body { position: absolute; left: 110px; right: 110px; top: 230px; bottom: 140px;
        display: flex; flex-direction: column; justify-content: center; gap: 36px; }
.footer { position: absolute; left: 110px; right: 110px; bottom: 50px; display: flex; gap: 10px; }
.footer div { flex: 1; border-top: 6px solid #DAD8D0; padding-top: 10px; font-size: 17px; color: #A3A097; font-weight: 600; }
.footer div.past { border-color: #9DB4EE; color: #6B7280; }
.footer div.cur { border-color: #2559D9; color: #1C1F26; }

[data-step] { opacity: 0; }
[data-step].on { opacity: 1; }
.multi [data-step].now.card, .multi .row.now { border-color: #2559D9; box-shadow: 0 0 0 3px rgba(37,89,217,.15); }
.multi .row.now { background: #EEF3FF; }
.multi [data-step].now.note, .multi [data-step].now.callout { border-color: #2559D9; }

.box { background: #fff; border: 2.5px solid #2B2F38; border-radius: 20px; padding: 28px 40px;
       font-size: 38px; font-weight: 650; text-align: center; }
.box small { font-size: 24px; font-weight: 500; color: #5B6070; }
.box.accent { border-color: #2559D9; background: #EEF3FF; }
.box.big { font-size: 46px; padding: 40px 56px; }
.box.sm { font-size: 30px; padding: 20px 30px; }
.note { font-size: 32px; line-height: 1.45; background: #fff; border-left: 8px solid #C9C6BC; border-radius: 12px; padding: 24px 32px; }
.callout { font-size: 36px; font-weight: 650; text-align: center; padding: 26px; border: 2.5px dashed #9AA3B5; border-radius: 16px; }
.code, .tree { font-family: "DejaVu Sans Mono", monospace; font-size: 30px; background: #1E222B; color: #E8EAF0;
        border-radius: 16px; padding: 26px 34px; white-space: pre; }
.code span, .tree span { color: #8B93A7; }
.code b { color: #8FB4FF; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: .9em; background: #E9E7E0; padding: 2px 8px; border-radius: 6px; }

.title-slide .body { justify-content: center; align-items: flex-start; }
.title-slide .big-title { font-size: 120px; font-weight: 800; letter-spacing: -3px; }
.subtitle { font-size: 46px; color: #3A3F4B; margin-top: 10px; }
.meta { font-size: 30px; color: #2559D9; font-weight: 600; margin-top: 30px; }

.agenda { display: grid; grid-template-columns: 1fr 1fr; gap: 22px 60px; }
.ag { font-size: 36px; font-weight: 550; display: flex; align-items: center; gap: 22px; }
.num { width: 58px; height: 58px; border-radius: 50%; background: #2559D9; color: #fff; display: inline-flex;
       align-items: center; justify-content: center; font-size: 28px; font-weight: 700; flex: none; }

.flow { display: flex; align-items: center; gap: 30px; justify-content: center; }
.arrows { display: flex; flex-direction: column; gap: 26px; width: 520px; }
.arrow { font-size: 26px; color: #4B5160; text-align: center; padding-bottom: 8px; border-bottom: 3px solid #4B5160; position: relative; }
.arrow.r::after { content: ""; position: absolute; right: -2px; bottom: -11px; border: 9px solid transparent; border-left: 16px solid #4B5160; }
.arrow.l::after { content: ""; position: absolute; left: -2px; bottom: -11px; border: 9px solid transparent; border-right: 16px solid #4B5160; }
.tokens { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 38px; justify-content: center; }
.tokens span { background: #fff; border: 2px solid #9DB4EE; border-radius: 10px; padding: 8px 16px; font-family: "DejaVu Sans Mono", monospace; font-size: 32px; }
.tk { width: 100%; text-align: center; font-size: 28px; color: #4B5160; margin-top: 8px; }

.gw { display: flex; align-items: center; justify-content: center; gap: 0; }
.gw .col { display: flex; flex-direction: column; gap: 22px; width: 330px; }
.gw .mid { display: flex; align-items: center; }
.gw .line { width: 110px; border-top: 3px solid #4B5160; }

.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; }
.grid2 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
.grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 24px; }
.card { background: #fff; border: 2.5px solid #DCD9CF; border-radius: 18px; padding: 30px 32px; }
.card .ct { font-size: 34px; font-weight: 750; margin-bottom: 12px; }
.card .cx { font-size: 27px; line-height: 1.4; color: #3A3F4B; }
.grid4 .card { text-align: center; }
.grid4 .card .ct { font-size: 64px; color: #2559D9; }

.table { display: flex; flex-direction: column; gap: 10px; }
.row { display: grid; grid-template-columns: 420px 1fr; gap: 30px; background: #fff; border: 2.5px solid #E2DFD6;
       border-radius: 14px; padding: 18px 28px; font-size: 29px; line-height: 1.35; }
.row.head { background: transparent; border: none; font-size: 22px; font-weight: 700; letter-spacing: 2px;
            text-transform: uppercase; color: #6B7280; padding-top: 0; padding-bottom: 0; }
.row .l { font-weight: 700; }

.imgwrap { background: #fff; border-radius: 22px; box-shadow: 0 6px 30px rgba(0,0,0,.08); padding: 20px;
           display: flex; justify-content: center; align-self: center; }
.imgwrap img { max-height: 660px; max-width: 1640px; display: block; }
.imgwrap.small img { max-height: 620px; max-width: 1080px; }
.caption { text-align: center; font-size: 26px; color: #4B5160; }

.fn { display: flex; align-items: center; justify-content: center; gap: 26px; }
.req, .clip { background: #fff; border: 2.5px solid #DCD9CF; border-radius: 18px; padding: 24px 30px; min-width: 430px; }
.req .ct, .clip .ct, .rot .ct, .script .ct { font-size: 26px; font-weight: 700; color: #2559D9; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 14px; }
.kv { font-family: "DejaVu Sans Mono", monospace; font-size: 26px; margin: 8px 0; }
.kv span { display: inline-block; width: 170px; color: #6B7280; }
.bigarrow { font-size: 70px; color: #4B5160; }

.chain { display: flex; gap: 18px; align-items: stretch; }
.cp { flex: 1; background: #fff; border: 2.5px solid #DCD9CF; border-radius: 18px; padding: 26px 20px; text-align: center; }
.cp:last-child { border-color: #2559D9; background: #EEF3FF; }
.cp .n { width: 50px; height: 50px; margin: 0 auto 14px; border-radius: 50%; background: #2559D9; color: #fff;
         display: flex; align-items: center; justify-content: center; font-size: 26px; font-weight: 700; }
.cp .ct { font-size: 30px; font-weight: 750; }
.cp .cx { font-family: "DejaVu Sans Mono", monospace; font-size: 22px; color: #6B7280; margin-top: 10px; }

.stream { display: flex; align-items: center; justify-content: center; gap: 22px; }
.drops { display: flex; gap: 8px; }
.drops span { background: #fff; border: 2px solid #9DB4EE; border-radius: 10px; padding: 8px 12px;
              font-family: "DejaVu Sans Mono", monospace; font-size: 26px; }

.split { display: flex; gap: 60px; align-items: center; justify-content: center; }
.rot { background: #fff; border: 2.5px solid #DCD9CF; border-radius: 18px; padding: 36px 44px; min-width: 560px; }
.att { font-size: 40px; font-weight: 650; margin: 18px 0; }
.att.dim { color: #A3A097; }
.att .x { color: #C2412D; } .att .ok { color: #1F8A4C; }
.rot .cx { font-size: 24px; color: #6B7280; margin-top: 10px; }
.codes { display: flex; gap: 20px; justify-content: center; flex-wrap: wrap; }
.codes span { background: #fff; border: 2.5px solid #DCD9CF; border-radius: 40px; padding: 14px 28px; font-size: 30px; }
.codes b { color: #2559D9; }
.big74 { font-size: 200px; font-weight: 800; color: #2559D9; line-height: 1; text-align: center; }
.big74 small { display: block; font-size: 38px; color: #3A3F4B; font-weight: 600; margin-top: 10px; }
.script { background: #fff; border: 2.5px solid #DCD9CF; border-radius: 18px; padding: 30px 36px;
          display: grid; grid-template-columns: auto 1fr; gap: 18px 26px; align-items: center; }
.script .ct { grid-column: 1 / 3; }
.script span { font-size: 30px; }
.script code { font-size: 28px; padding: 6px 14px; }
.k8s { display: flex; justify-content: center; }
.kbox { border: 3px dashed #2559D9; border-radius: 26px; padding: 36px 50px; text-align: center; width: 1100px; }
.kbox .ct { font-size: 44px; font-weight: 750; color: #2559D9; }
.kbox .cx { font-size: 28px; color: #3A3F4B; margin: 10px 0 30px; }
.ctr { background: #fff; border: 2.5px solid #2B2F38; border-radius: 18px; padding: 34px; font-size: 40px; font-weight: 700; }
.ctr small { font-size: 26px; font-weight: 500; color: #5B6070; }
.steps { display: flex; flex-direction: column; gap: 20px; }
.st { display: flex; align-items: center; gap: 28px; font-size: 36px; background: #fff; border: 2.5px solid #DCD9CF;
      border-radius: 16px; padding: 22px 30px; }
.st .n { width: 58px; height: 58px; border-radius: 50%; background: #2559D9; color: #fff; display: flex; align-items: center;
         justify-content: center; font-size: 28px; font-weight: 700; flex: none; }
.recap { display: flex; flex-direction: column; gap: 18px; }
.rc { font-size: 36px; background: #fff; border: 2.5px solid #E2DFD6; border-radius: 16px; padding: 22px 32px; line-height: 1.35; }
.multi .rc.now { border-color: #2559D9; }
.multi .st.now, .multi .cp.now { border-color: #2559D9; }
"""


def page(slide: dict, step: int) -> str:
    n_steps = len(slide["steps"])
    ci = CHAPTERS.index(slide["chapter"])
    footer = "".join(
        f'<div class="{"cur" if i == ci else "past" if i < ci else ""}">{c}</div>'
        for i, c in enumerate(CHAPTERS)
    )
    body = slide["body"]
    kind = slide.get("kind", "")
    if kind == "title":
        head = f'<div class="big-title">{slide["title"]}</div>'
        inner = f'<div class="body">{head}{body}</div>'
    else:
        inner = (
            f'<div class="header"><div class="chapter">{slide["chapter"]}</div>'
            f'<div class="title">{slide["title"]}</div></div><div class="body">{body}</div>'
        )
    classes = ("title-slide " if kind == "title" else "") + ("multi" if n_steps > 1 else "")
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>'
        f'<body class="{classes}">{inner}<div class="footer">{footer}</div>'
        "<script>document.querySelectorAll('[data-step]').forEach(e => {"
        f"const s = +e.dataset.step; if (s <= {step}) e.classList.add('on');"
        f"if (s === {step}) e.classList.add('now'); }});</script></body></html>"
    )


def units(only: int | None):
    for si, slide in enumerate(SLIDES):
        if only is not None and si != only:
            continue
        for k, text in enumerate(slide["steps"], start=1):
            yield si, k, slide, text


def render(only: int | None) -> None:
    from playwright.sync_api import sync_playwright

    FRAMES.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME) if CHROME else p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1920, "height": 1080})
        for si, k, slide, _ in units(only):
            html = OUT / "page.html"
            html.write_text(page(slide, k))
            pg.goto(html.as_uri())
            pg.wait_for_load_state("networkidle")
            pg.screenshot(path=str(FRAMES / f"{si:02d}_{k:02d}.png"))
        browser.close()
    print("rendered")


def tts(only: int | None, voice: str) -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    if voice == "piper":
        tts_piper(only)
        return
    import soundfile as sf
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(VOICES / "kokoro-v1.0.onnx"), str(VOICES / "voices-v1.0.bin"))
    lang = "en-gb" if voice.startswith("b") else "en-us"
    for si, k, _, text in units(only):
        audio, sr = kokoro.create(text, voice=voice, speed=1.0, lang=lang)
        sf.write(str(AUDIO / f"{si:02d}_{k:02d}.wav"), audio, sr, subtype="PCM_16")
    print(f"tts done ({voice})")


def tts_piper(only: int | None) -> None:
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(str(VOICES / "en_US-lessac-high.onnx"))
    cfg = SynthesisConfig(length_scale=1.06, noise_scale=0.6, noise_w_scale=0.8)
    for si, k, _, text in units(only):
        out = AUDIO / f"{si:02d}_{k:02d}.wav"
        with wave.open(str(out), "wb") as wf:
            first = True
            for chunk in voice.synthesize(text, syn_config=cfg):
                if first:
                    wf.setnchannels(chunk.sample_channels)
                    wf.setsampwidth(chunk.sample_width)
                    wf.setframerate(chunk.sample_rate)
                    first = False
                else:  # a short pause between sentences
                    wf.writeframes(b"\x00" * int(chunk.sample_rate * 0.28) * chunk.sample_width)
                wf.writeframes(chunk.audio_int16_bytes)
    print("tts done (piper)")


def duration(path: Path) -> float:
    with wave.open(str(path)) as wf:
        return wf.getnframes() / wf.getframerate()


def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def video() -> None:
    SEGS.mkdir(parents=True, exist_ok=True)
    concat, subs, chapters = [], [], []
    t = 0.0
    last_chapter = None
    for si, k, slide, text in units(None):
        wav = AUDIO / f"{si:02d}_{k:02d}.wav"
        img = FRAMES / f"{si:02d}_{k:02d}.png"
        speech = duration(wav)
        pad = STEP_PAD + (SLIDE_PAD if k == len(slide["steps"]) else 0)
        total = speech + pad
        seg = SEGS / f"{si:02d}_{k:02d}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(img),
                "-i", str(wav), "-af", f"apad=pad_dur={pad},aresample=48000", "-t", f"{total:.3f}",
                "-c:v", "libx264", "-tune", "stillimage", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ac", "1", str(seg),
            ],
            check=True,
        )
        concat.append(f"file '{seg}'")
        if slide["chapter"] != last_chapter:
            chapters.append((t, slide["chapter"]))
            last_chapter = slide["chapter"]
        # Subtitles: split the step into sentences, timed by length.
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
        chars = sum(len(x) for x in sentences)
        st = t
        for sentence in sentences:
            d = speech * len(sentence) / chars
            subs.append((st, st + d, sentence))
            st += d
        t += total
    (OUT / "concat.txt").write_text("\n".join(concat) + "\n")
    srt = "\n".join(
        f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(subs, 1)
    )
    srt = srt.replace("L L M", "LLM").replace("A P I", "API").replace("A I ", "AI ").replace("E K S", "EKS")
    srt = srt.replace("V P C", "VPC").replace("E C R", "ECR").replace("I D", "ID").replace("G W", "GW")
    srt = srt.replace("Fast A P I", "FastAPI").replace("K one", "K1").replace("K two", "K2")
    (OUT / "llm-gateway-explained.srt").write_text(srt)
    meta = ";FFMETADATA1\ntitle=The LLM Gateway, explained\n" + "".join(
        f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(a * 1000)}\nEND={int((chapters[i + 1][0] if i + 1 < len(chapters) else t) * 1000)}\ntitle={name}\n"
        for i, (a, name) in enumerate(chapters)
    )
    (OUT / "chapters.txt").write_text(meta)
    final = OUT / "llm-gateway-explained.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "concat.txt"),
            "-i", str(OUT / "llm-gateway-explained.srt"), "-i", str(OUT / "chapters.txt"),
            "-map", "0:v", "-map", "0:a", "-map", "1:s", "-map_metadata", "2", "-map_chapters", "2",
            "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
            "-movflags", "+faststart", str(final),
        ],
        check=True,
    )
    print(json.dumps({"seconds": round(t, 1), "chapters": [(round(a), n) for a, n in chapters]}))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    only = int(sys.argv[sys.argv.index("--only") + 1]) if "--only" in sys.argv else None
    voice = sys.argv[sys.argv.index("--voice") + 1] if "--voice" in sys.argv else "am_michael"
    if cmd in ("render", "all"):
        render(only)
    if cmd in ("tts", "all"):
        tts(only, voice)
    if cmd in ("video", "all"):
        video()
