# Explainer video

A 16-minute narrated walkthrough of the project for a lightly technical audience: what an LLM
gateway is, why it exists, how people coped before, the first-principles building blocks, the
HLD, the LLD, and the AWS infrastructure. Chapters are embedded in the MP4, and English subtitles
are both embedded and written to an `.srt` file.

- `slides.py`: the script. Each slide has a chapter, a title, an HTML body, and narration steps;
  elements marked `data-step="n"` appear when step *n* is spoken. The design and infrastructure
  chapters reuse the diagrams in `docs/img/`.
- `build.py`: renders each step to a 1920×1080 PNG with headless Chromium, synthesizes the
  narration, and assembles the MP4 with ffmpeg.

## Build

Needs Python 3.12+, ffmpeg, and a Chromium that Playwright can drive.

```bash
cd docs/video
uv venv && uv pip install -r requirements.txt     # or: python -m venv .venv && pip install ...
mkdir -p voice
curl -L -o voice/kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o voice/voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

.venv/bin/python build.py all                      # default voice: af_heart
.venv/bin/python build.py tts --voice bm_george && .venv/bin/python build.py video   # change voice
.venv/bin/python build.py render --only 5          # re-render one slide after editing slides.py
```

Output goes to `out/llm-gateway-explained.mp4` and `.srt` (git-ignored). Set `CHROME_PATH` to use
a specific Chromium binary. Rendering takes about a minute, narration about four, and assembly
about six on a laptop-class CPU.

Narration is written for speech: acronyms are spelled out ("L L M", "E K S") so the voice says
them letter by letter; `build.py` folds them back for the subtitles.
