"""thin, shell-free wrappers around ffmpeg / ffprobe / exiftool."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np


class ToolError(RuntimeError):
    pass


def quiet() -> dict:
    """subprocess options for a windowed (no-console) windows app: no flashing console
    windows for ffmpeg, and no inherited (invalid) stdin handle."""
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kw


def _bundled(name: str) -> str | None:
    """ffmpeg/ffprobe shipped next to the packaged app (windows build)."""
    import sys
    if getattr(sys, "frozen", False):
        for cand in (Path(sys.executable).parent / f"{name}.exe",
                     Path(sys.executable).parent / "bin" / f"{name}.exe"):
            if cand.exists():
                return str(cand)
    return None


def tool(name: str) -> str:
    env = os.environ.get(f"AUREON_{name.upper()}")
    path = env or _bundled(name) or shutil.which(name)
    if not path:
        raise ToolError(f"{name} not found on PATH (or set AUREON_{name.upper()})")
    return path


def has_tool(name: str) -> bool:
    try:
        tool(name)
        return True
    except ToolError:
        return False


def run(args: list[str], *, input: bytes | None = None, capture: bool = True) -> bytes:
    """run a command given as an argument list (never through a shell)."""
    kw = quiet()
    if input is not None:
        kw.pop("stdin")
    proc = subprocess.run(
        args,
        input=input,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **kw,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[-2000:]
        raise ToolError(f"{Path(args[0]).name} failed ({proc.returncode}): {err}")
    return proc.stdout if capture else b""


def probe(path: str | Path) -> dict:
    out = run([
        tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-show_chapters", str(path),
    ])
    return json.loads(out)


def exif(path: str | Path) -> dict:
    if not has_tool("exiftool"):
        return {}
    out = run([tool("exiftool"), "-j", "-a", "-G1", "-n", str(path)])
    data = json.loads(out)
    return data[0] if data else {}


def load_audio(path: str | Path, sr: int = 48000, channels: int | None = None) -> np.ndarray:
    """decode the first audio stream to float32, shape (n, ch)."""
    args = [tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-vn"]
    if channels:
        args += ["-ac", str(channels)]
    args += ["-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"]
    raw = run(args)
    a = np.frombuffer(raw, dtype=np.float32)
    ch = channels or audio_channels(path)
    return a.reshape(-1, ch).copy()


def audio_channels(path: str | Path) -> int:
    info = probe(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            return int(s.get("channels", 1))
    raise ToolError("no audio stream")


def write_wav(path: str | Path, audio: np.ndarray, sr: int) -> None:
    """write float audio as 24-bit wav through ffmpeg (keeps it dependency-free)."""
    audio = np.ascontiguousarray(np.clip(audio, -1.0, 1.0).astype(np.float32))
    ch = audio.shape[1] if audio.ndim == 2 else 1
    run([
        tool("ffmpeg"), "-v", "error", "-nostdin", "-y",
        "-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", "pipe:0",
        "-c:a", "pcm_s24le", "-fflags", "+bitexact", "-map_metadata", "-1", str(path),
    ], input=audio.tobytes(), capture=False)


def read_frames(path: str | Path, fps: float, width: int):
    """yield (t, rgb uint8 frame) at a low sample rate, scaled to `width`."""
    info = probe(path)
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w, h = display_size(v)
    height = max(2, int(round(width * h / w / 2)) * 2)
    proc = subprocess.Popen(
        [tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:v:0",
         "-vf", f"fps={fps},scale={width}:{height}:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **quiet(),
    )
    size = width * height * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield (i + 0.5) / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


def display_size(stream: dict) -> tuple[int, int]:
    """width/height after rotation metadata is applied."""
    w, h = int(stream["width"]), int(stream["height"])
    rot = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(stream.get("tags", {}).get("rotate", rot))
    if abs(rot) % 180 == 90:
        w, h = h, w
    return w, h
