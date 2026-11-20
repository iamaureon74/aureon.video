# aureon.video — plan v0.1

pipeline: upload → probe → transcribe (local) → clean audio → strip + re-encode → render transcript overlay → verify → download.

## 1. metadata + fingerprint stripping

| layer | what leaks | fix | how sure |
|---|---|---|---|
| container | gps (`©xyz`, `location`), device make/model/serial, creation time, software tags, chapters | `ffmpeg -map_metadata -1 -map_chapters -1 -fflags +bitexact`, drop data/timecode/subtitle streams | solid |
| encoder | x264/hevc settings strings, apple/samsung private atoms | full re-encode with neutral settings | solid |
| provenance | c2pa content credentials, platform watermarks | re-encode drops c2pa manifests; invisible pixel watermarks are partly or fully removed by rescale + re-encode | partial |
| sensor (prnu) | camera noise pattern that identifies one specific device | light denoise + small rescale/crop. this weakens matching but does not guarantee it is gone | partial, research item |
| visual content | landmarks, signs, sun angle, plates | out of scope; warn the user in the ui | not handled |

verification: `exiftool -a -G1` and `ffprobe -show_format -show_streams` on the output must show no identifying fields. this check runs automatically and its report is shown to the user.

## 2. hidden audio signals

what is real:
- **enf (electrical network frequency)**: mains hum at 50/60 hz plus harmonics. investigators match it against power-grid logs to work out when and where audio was recorded. it is well documented and the main real threat.
- **ultrasonic beacons**: 18–22 khz tones used by ad trackers (e.g. silverpush) to link devices.
- **infrasound / sub-bass** below about 20 hz.
- **aircraft doppler geolocation** (demonstrated nov 2026; see 2a below).

clarification: planes and satellites don't transmit anything into recordings. a passing plane's engine sound is what gets recorded, and its doppler pattern can be matched against public flight-tracking data.

### 2a. aircraft doppler geolocation

how it works:
- **the doppler curve**: a passing plane's engine and propeller tones slide down in pitch as it flies over. this leaves an s-shaped curve in the audio's spectrogram.
- **what the curve reveals**: its shape gives the plane's speed and its distance at the closest point. this is documented passive-acoustics science (b.g. ferguson, jasa 1992/1993).
- **the matching step**: the curve is matched against public ads-b flight tracks (adsb.lol, opensky, flightradar24) to estimate where the recording was made.
- **demonstrated results**: the youtuber colsto showed this in november 2026, reportedly to within about 100 m, sometimes from less than a minute of audio. the open-source tool `doppler-locate` reproduces it.
- **weak points**: it needs the plane to appear in ads-b coverage. wind, city noise and different microphones haven't been tested much. it hasn't been peer-reviewed yet.
- **reported survival**: the pattern reportedly survived high-pass filtering and phone compression, so the basic filters in section 2 do **not** defeat it.

countermeasures (all classical dsp except mode c):
- **detect**: track tonal ridges in the short-time fourier transform (stft) and flag narrowband tones whose frequency slides along a doppler-shaped curve. a match is defined as a tanh-like hyperbolic fit with residual below threshold. the ui shows these as "aircraft signatures found".
- **mode a, tracking notch**: a notch filter follows each detected tone's instantaneous frequency, plus harmonics. this keeps the room ambience.
- **mode b, warp**: a random, smooth time-warp of about ±3% and a pitch micro-modulation. this distorts the curve's shape and timing so it no longer fits real flight tracks. it is combined with mode a.
- **mode c, voice-only**: isolate speech with source separation (demucs, an ai model run locally) and drop all background audio. this is the strongest option, but the ambience is lost.
- **timestamps**: stripping the creation time (section 1) already widens the ads-b search window a lot.

verification: run `doppler-locate` against the output as an automated red-team test. pass = no candidate location within 5 km.

### 2b. filter chain
1. high-pass at about 80 hz (removes infrasound and the 50/60 hz fundamental)
2. adaptive notch comb at the detected enf frequency and its harmonics up to about 1 khz
3. aircraft tracking notch and warp (2a). in voice-only mode, speech isolation replaces this step.
4. low-pass at about 16 khz (removes ultrasonic beacons)
5. resample to 48 khz and add a small random time-stretch/dither so leftover enf traces no longer line up with grid logs
6. before/after spectrogram in the report as proof

sources: ferguson 1992 (jasa) via auditory.org/asamtgs/asa92nwo/1pEA/1pEA10.html; gizmodo, ibtimes, pasqualepillitteri.it and levelup.gitconnected.com coverage of the colsto demonstration (nov 2026).

## 3. transcription (local only)

sending audio to a cloud api would undo the privacy work, so this step runs on the user's machine or server.
- whisper large-v3 (or turbo) through **whisperx**, with wav2vec2 forced alignment for word-level timestamps
- optional vad and diarization
- this step uses ai. the non-ai requirement applies to the overlay logic in section 4.

## 4. overlay engine (deterministic, no ai)

1. **probe** width, height, fps and rotation. classify the video as landscape, portrait or square.
2. **text box**: right side, vertically centred. width is about 32% of frame width for landscape and about 45% for portrait, with a 5% safe margin. font size scales with frame height.
3. **colour match** for each transcript segment:
   - sample the box region's pixels, convert to oklab, find dominant colours with k-means (k=3). this is classical clustering, not ai.
   - text colour = the dominant hue, with lightness pushed until wcag contrast against the local background is at least 4.5:1
   - smooth colour over time with an exponential moving average (ema) so it does not flicker between scenes
4. **3-row lyric layout**, like spotify's synced lyrics:
   - top row: past line, 40% opacity
   - middle row: active line, 100% opacity, word-by-word highlight
   - bottom row: next line, 40% opacity
   - rows scroll up with a 200–300 ms ease
5. **render**: generate an ass subtitle file (`\k` karaoke tags, per-line colours, the font embedded) and burn it in with ffmpeg/libass in the same pass as the clean re-encode.

## 5. font

the screenshot looks like a geometric sans in the century gothic / itc avant garde family: perfect-circle o/d and a single-storey "a". this is an inference from the shapes, not a confirmed match.
- free legal match: **urw gothic** (open-licensed avant garde clone), or **didact gothic**
- if you have the original font file, use that instead, after checking its licence

## 6. architecture

- **phase 1 — engine**: python cli. dependencies: ffmpeg, whisperx, numpy, opencv. `aureon clean in.mp4 -o out.mp4`
- **phase 2 — app**: web ui for uploads, plus a worker on a gpu host. uploads are deleted after processing and no logs are kept. vercel can host the ui but cannot run ffmpeg or whisper itself.
- **phase 3**: batch jobs, presets, an optional soft-subtitle (non-burned) export

## open decisions
1. local desktop/cli tool or hosted web app?
2. burned-in text only, or also a soft subtitle track?
3. languages: english only or multilingual?
4. original font file available?
