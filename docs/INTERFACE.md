# aureon.video — interface plan v0.1

the brand comes from the reference screenshot: "void — a little less noise. a little more possibility." the product's job is removal, so the interface should feel quiet, dark and calm.

## 1. windows build

| part | choice | why |
|---|---|---|
| app shell | **tauri 2** (rust + webview2) | ~10 mb installer vs ~150 mb electron; enforced permission model for the ui ↔ native bridge |
| ui | svelte or react + typescript, vite | fast, small |
| engine | python, packaged with pyinstaller as a tauri **sidecar** | whisperx, numpy and opencv live here |
| media | ffmpeg (lgpl build) bundled as a sidecar | decode, filter, render, encode |
| transcription | faster-whisper / whisperx. **cuda** on nvidia gpus, otherwise int8 on cpu | runs offline |
| models | large-v3-turbo (~800 mb), downloaded on first run and **sha-256 verified**. optional "offline installer" bundles the model | keeps the base installer small |
| installer | nsis `.exe` + `.msi` (tauri bundler) | standard |
| signing | authenticode: azure trusted signing or an ev certificate | avoids smartscreen "unknown publisher" warnings |
| target | windows 10 22h2+ / 11, x64 (arm64 later) | webview2 is built in |

## 2. user flow (5 screens, one window)

```
[1 drop] → [2 scan] → [3 choose] → [4 process] → [5 proof]
```

1. **drop**: empty dark field with one thin ring in the centre. text: "drop a video into the void." click to browse. accepts mp4, mov, mkv, webm.
2. **scan** (read-only; nothing is changed yet): findings appear **as each detector actually finishes**:
   - metadata: lists real fields found, e.g. `gps 34.05, -118.24`, `device: iphone 15 pro`, `created: 2026-11-20 14:02`
   - audio: `mains hum 60 hz detected`, `ultrasonic tone 19.4 khz`, `2 aircraft doppler signatures` (each with a spectrogram thumbnail)
   - provenance: `c2pa manifest present` / `none`
   - summary line: "11 things could identify this video."
3. **choose**:
   - audio protection: `standard` / `standard + aircraft warp` / `voice only (strongest)`
   - transcript overlay: on/off, language (auto-detect), font
   - live preview player showing the 3-row overlay on the real video
4. **process**: real stages with real progress. ffmpeg frame count drives the bar, and each stage is ticked only when it finishes:
   `transcribing → cleaning audio → stripping → rendering → verifying`
   cancel works at any point.
5. **proof**: before/after report:
   - metadata table: every removed field shown struck through
   - spectrogram before/after with a drag wipe slider
   - red-team results: `doppler-locate: no location found ✓`, `exiftool: 0 identifying fields ✓`
   - prnu note: "sensor fingerprint weakened, not guaranteed removed"
   - buttons: `save video`, `save report (pdf)`, `process another`

other screens: settings, plus a first-run model download screen with real bytes/sec progress.

## 3. visual system

**colour tokens** (dark first; a light theme comes later)
| token | value | use |
|---|---|---|
| `--void` | `#0b0a09` | background |
| `--ash` | `#16130f` | panels |
| `--smoke` | `#2a241c` | borders, dividers |
| `--bone` | `#ece6dc` | primary text |
| `--dust` | `#8f857a` | secondary text, past/future lyric rows |
| `--ember` | `#c9a46a` | accent: the ring, focus, active stage |
| `--clear` | `#7fb89a` | verified / removed ✓ |
| `--signal` | `#d9735b` | found / risk |

all text pairs are checked against wcag aa (4.5:1). colour is never the only signal; every state also gets an icon and a word.

**type**
- display: urw gothic book (thin and geometric, as in the screenshot), wide tracking `0.25em`, lower-case
- ui text: urw gothic demi, 14/20
- data (fields, hz, coordinates): jetbrains mono 13
- scale: 12 / 14 / 18 / 28 / 56

**layout**
- 8 px grid, 24 px panel padding, minimum window 960×640, custom frameless title bar
- the ring stays the central motif on every screen: drop target, progress indicator, done state

## 4. motion

rules: every animation shows something real happening, never blocks input, and is replaced by fades under `prefers-reduced-motion`.

| moment | motion | duration / easing | what it means |
|---|---|---|---|
| hover with file | ring widens and its stroke glows ember | 180 ms ease-out | "i'll take it" |
| drop | thumbnail shrinks into the ring centre | 420 ms cubic-bezier(.2,.8,.2,1) | file is entering the void |
| scan finding | row fades up 8 px, red dot pulses once | 220 ms each, appears when the detector finishes | real discovery |
| processing | ring stroke fills clockwise, driven by real frame count; current stage label crossfades | continuous | true progress |
| field stripped | strike-through draws left → right, then the row dims | 300 ms | removal made visible |
| done | ring closes, a single soft ember bloom | 600 ms | completion |
| lyric overlay | rows slide up one line height; words light up on their timestamps | 250 ms ease-out | active / past / future |
| spectrogram compare | user drags a wipe; no auto-play | direct manipulation | before vs after |

no fake spinners, no invented percentages, no confetti.

## 5. favicon / app icon

concept: **the open ring**, the "o" from void with a small gap at 2 o'clock. it reads as "a circle with something taken out", a void that lets nothing out.
- ember `#c9a46a` ring on `#0b0a09` rounded square (windows tile) or transparent (favicon)
- stroke weight scales by size: 16 px → 2 px stroke, gap 30°; 256 px → 14 px stroke, gap 22°
- the `.ico` contains 16, 24, 32, 48, 64 and 256 px sizes, each hand-hinted at 16/24 so the gap stays visible
- also: `favicon.svg`, `apple-touch-icon` 180 px and a monochrome taskbar variant

## 6. security (desktop)

| area | measure |
|---|---|
| privacy | 100% local. no telemetry, no analytics, no crash upload. network is used only for the optional model download and update check, each a visible toggle |
| originals | never modified; output is always a new file |
| filename leak | output named `void_<random 8>.mp4` by default (names like `IMG_20261120_1402.mov` leak the capture time) |
| file timestamps | output's created/modified times set to the processing time, not copied |
| temp files | per-job folder in `%LOCALAPPDATA%\aureon\jobs\<uuid>`, deleted on finish or crash recovery. note: secure wipe is not reliable on ssds, so temp audio/frames are kept in memory where feasible |
| hostile video files | ffmpeg decoders are an attack surface: pinned and patched ffmpeg; sidecars run in a windows job object with no network access and restricted to the job folder |
| ipc | tauri capabilities allow only named commands; strict csp in the webview; no remote content loaded |
| command injection | sidecar args passed as arrays, never through a shell; paths validated |
| supply chain | lockfiles, hash-pinned python wheels, model sha-256 check, signed binaries |
| updates | tauri updater with ed25519 signatures; **off by default**, user opts in |

## 7. accessibility
- fully keyboard-driven (tab order matches the flow; `ctrl+o` opens a file, `esc` cancels)
- screen-reader labels on every finding and stage
- 44×44 px minimum targets; text scales to 200%
- lyric overlay colours meet aa contrast by algorithm (section 4 of PLAN.md)

## open decisions
1. svelte or react (no strong preference; svelte = smaller)
2. light theme now or later
3. signing route: azure trusted signing (cheaper) vs ev certificate
