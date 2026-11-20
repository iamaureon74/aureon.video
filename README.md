<p align="center">
  <a href="https://github.com/iamaureon74/aureon.video/releases/latest/download/aureon-windows-x64.zip">
    <img alt="download for windows" src="https://img.shields.io/badge/%E2%AC%87%20download%20for%20windows-.zip%20%C2%B7%20v0.1.0-996515?style=for-the-badge&labelColor=0b0a09" height="56">
  </a>
</p>

<p align="center"><a href="https://github.com/iamaureon74/aureon.video/releases/latest/download/aureon-windows-x64.zip"><b>⬇ click here to download aureon for windows</b></a></p>

# aureon.video — void

*a little less noise. a little more possibility.*

## how to install on windows

1. unzip it
2. open the `aureon` folder and double-click **`aureon.exe`**
3. the app opens in your browser. drop a video in, click to browse, or paste it with ctrl+v

if windows says "windows protected your pc": click **more info → run anyway**. the app
isn't code-signed yet. the transcription model (~800 mb) downloads the first time you use it.

[all releases](https://github.com/iamaureon74/aureon.video/releases)

drop in a video. aureon removes what could identify where, when and on what it was
recorded, adds a synced 3-row transcript on the right side, then re-checks its own output
and shows you the proof. everything runs on your computer and nothing is uploaded.

## what it does

| layer | removed | verified by |
|---|---|---|
| metadata | gps / location, device make & model, software version, creation dates, titles, comments, chapters, data tracks | ffprobe + exiftool re-scan of the output |
| encoder fingerprints | x264 settings string (sei), `Lavf` / `Lavc` tags, apple / android keys, compressor name | byte scan of the output |
| mains hum (enf) | 50/60 hz and every harmonic to 1 khz | detector re-run on the output: no hum peak within 20 db of the recording's noise floor |
| ultrasonic beacons | everything above 16 khz | detector re-run |
| aircraft doppler signatures | engine/propeller tones that trace a fly-over doppler curve (can be matched to ads-b flight logs) | detector re-run, including a pass on the final encoded audio |
| file name | `IMG_20261120_0142.mov` → `void_3f9a1c2e.mp4` | — |
| camera sensor noise (prnu) | weakened by a 2 % rescale + crop + light denoise | **not guaranteed** |

what it does **not** change: anything visible in the picture (houses, signs, skyline,
faces, sun angle). those can still give a location away.

## transcript overlay

- **transcription:** local speech-to-text with `faster-whisper` `large-v3-turbo` and
  word-level timestamps. it uses the gpu if there is one.
- **3 rows, spotify-lyrics style:**
  - past line on top (dim)
  - active line in the middle: each word fills and briefly glows the moment it is spoken
  - next line underneath (dim)
  - rows slide up 250 ms per line
- **font:** urw gothic (open-licensed century gothic / avant garde-style face). bundled in
  `engine/aureon/fonts` with its licence.
- **colour:**
  - golden brown (`#996515`) by default. the hue stays fixed and only the lightness adapts
    to the pixels behind the text, so every line meets wcag aa contrast (4.5:1).
  - on mid-tone scenes where no fill colour reaches 4.5:1, the text gets a dark halo.
  - `--color auto` matches the scene's own hue instead.
- **placement:** right side, vertically centred, sized from the video's real resolution.
  - 9:16 video automatically keeps clear of the tiktok / reels / shorts button column on
    the right edge.
  - tested at 1080×1920, 720×1280, 1080×1350, 1080×1080, 1920×1080, 3840×2160,
    2560×1080 and 334×188.

## use it

needs python 3.10+ and ffmpeg (with libass) on PATH. exiftool is optional but recommended.

```bash
cd engine
pip install -e ".[test]"
```

interface (drag and drop, click to browse, or paste with ctrl+v):

```bash
aureon app
```

command line:

```bash
aureon scan  my_video.mov
aureon clean my_video.mov -o out/
aureon clean my_video.mov -o out/ --audio voice --platform tiktok --color auto
```

`--audio`:
- `standard`: hum, ultrasonic and aircraft removal
- `warp`: adds a small bounded time-warp
- `voice`: keeps speech only

every run writes `void_xxxxxxxx.mp4` plus a `.report.md` and a `.report.json` listing what
was found, what was removed and each check's pass/fail.

## windows

`.github/workflows/windows.yml` builds a portable windows app on every push to `main`:
- runs the unit tests on windows
- packages `aureon.exe` with ffmpeg next to it
- smoke-tests the packaged exe on a generated video
- uploads it as the `aureon-windows-x64` artifact

double-clicking `aureon.exe` opens the interface. the whisper model (~800 mb) downloads
on first use.

## tests

```bash
cd engine && python -m pytest -q
```

81 tests, covering:
- detection and removal for each signal
- false-positive guards (steady tones, rising chirps, noise)
- a/v sync bounds for the warp
- colour contrast on 50 random backgrounds
- layout at 8 frame sizes
- ass escaping
- a full end-to-end clean of a video built with every threat in it

## layout

```
docs/PLAN.md           threat research + plan
docs/INTERFACE.md      interface, motion, icon, security spec
engine/aureon/         the engine (meta, audio, color, overlay, transcribe, pipeline, app)
engine/aureon/web/     the local interface
engine/tests/          tests + threat fixture generator
```
