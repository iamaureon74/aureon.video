"""build a test video that carries every threat the engine handles:
gps + device metadata, chapters, mains hum, an ultrasonic beacon and an aircraft fly-over."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from aureon import audio as A
from aureon import ff

SR = 48000
SPEECH = ("testing aureon video. a little less noise, a little more possibility. "
          "we are standing on a quiet street at dusk. the lights are on in every house. "
          "nothing in this recording should tell anyone where it was made.")


def flyover(n, sr, f0=110.0, speed=80.0, dist=250.0, t0=6.0, harmonics=3):
    t = np.arange(n) / sr
    f = A.doppler_curve(t, f0, speed / A.SPEED_OF_SOUND, t0, dist / speed)
    phase = 2 * np.pi * np.cumsum(f) / sr
    r = np.sqrt((speed * (t - t0)) ** 2 + dist ** 2)
    amp = (dist / r) ** 1.2
    y = sum(np.sin(k * phase) / k for k in range(1, harmonics + 1))
    return 0.05 * amp * y


def speech(text, sr):
    if not shutil.which("espeak-ng"):
        return None
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "s.wav"
        subprocess.run(["espeak-ng", "-s", "150", "-w", str(wav), text], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        x = ff.load_audio(wav, sr, channels=1)[:, 0]
    return x / (np.max(np.abs(x)) + 1e-9) * 0.5


def build_audio(duration, sr=SR, with_speech=True, seed=1):
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    if with_speech:
        s = speech(SPEECH, sr)
        if s is not None:
            s = s[: n - int(0.8 * sr)]
            y[int(0.8 * sr): int(0.8 * sr) + len(s)] += s
    y += 0.012 * (np.sin(2 * np.pi * 60 * t) + 0.5 * np.sin(2 * np.pi * 120 * t)
                  + 0.3 * np.sin(2 * np.pi * 180 * t))          # mains hum
    y += 0.01 * np.sin(2 * np.pi * 19500 * t)                     # ultrasonic beacon
    y += flyover(n, sr, t0=duration / 2)                          # aircraft
    y += 0.002 * rng.standard_normal(n)                           # room noise
    return np.stack([y, y], axis=1).astype(np.float32)


def build_video(image: Path, out: Path, duration=14.0, size=(1280, 720), with_speech=True):
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        wav = d / "a.wav"
        ff.write_wav(wav, build_audio(duration, with_speech=with_speech), SR)
        meta = d / "meta.txt"
        meta.write_text(";FFMETADATA1\ntitle=walk home\ncomment=shot on my phone\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=5000\ntitle=intro\n")
        w, h = size
        subprocess.run([
            ff.tool("ffmpeg"), "-v", "error", "-y", "-loop", "1", "-framerate", "30",
            "-i", str(image), "-i", str(wav), "-i", str(meta),
            "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2",
            "-t", str(duration),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                   f"crop={w}:{h},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "256k",
            "-movflags", "+use_metadata_tags",
            "-metadata", "location=+34.0522-118.2437/",
            "-metadata", "com.apple.quicktime.make=Apple",
            "-metadata", "com.apple.quicktime.model=iPhone 15 Pro",
            "-metadata", "com.apple.quicktime.software=17.5.1",
            "-metadata", "creation_time=2026-11-20T01:42:07Z",
            str(out),
        ], check=True)
    return out
