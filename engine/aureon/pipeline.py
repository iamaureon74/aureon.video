"""scan → transcribe → clean audio → strip + re-encode with overlay → verify."""
from __future__ import annotations

import json
import secrets
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from . import audio as A
from . import ff, meta, overlay

SR = 48000


def _video_stream(info):
    return next((s for s in info["streams"] if s.get("codec_type") == "video"), None)


def _has_audio(info):
    return any(s.get("codec_type") == "audio" for s in info["streams"])


def scan(path: str | Path) -> dict:
    """read-only report of everything that could identify the video."""
    path = Path(path)
    m = meta.scan(path)
    out = {"file": path.name, "metadata": m["findings"], "audio": None}
    if _has_audio(m["probe"]):
        x = ff.load_audio(path, SR)
        a = A.scan(x, SR)
        a.pop("_aircraft_raw")
        out["audio"] = a
    v = _video_stream(m["probe"])
    if v:
        w, h = ff.display_size(v)
        out["video"] = {"width": w, "height": h, "codec": v.get("codec_name")}
    out["summary"] = summarize(out)
    return out


def summarize(s: dict) -> dict:
    n_meta = len(s["metadata"])
    a = s.get("audio") or {}
    n_audio = int(bool(a.get("enf", {}).get("detected"))) + \
        int(bool(a.get("ultrasonic", {}).get("detected"))) + len(a.get("aircraft", []))
    return {"metadata_items": n_meta, "audio_signals": n_audio, "total": n_meta + n_audio}


def clean(src: str | Path, out_dir: str | Path, *, audio_mode="standard", overlay_on=True,
          model: str | None = None, language: str | None = None, prnu=True,
          keep_name=False, lowercase=False, crf=18, log=print, seed=None,
          text_color: str = "golden-brown", platform: str = "auto") -> dict:
    src = Path(src).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem + "_clean" if keep_name else "void_" + secrets.token_hex(4)
    out_path = out_dir / f"{stem}.mp4"
    job = Path(tempfile.mkdtemp(prefix="aureon-"))
    t_start = time.time()
    report: dict = {"input": src.name, "output": out_path.name, "audio_mode": audio_mode}
    try:
        log("scanning")
        before = scan(src)
        report["before"] = before
        info = meta.ff.probe(src)
        v = _video_stream(info)
        if v is None:
            raise ff.ToolError("no video stream")
        W, H = ff.display_size(v)
        W, H = W // 2 * 2, H // 2 * 2
        has_audio = _has_audio(info)

        wav = None
        words = []
        if has_audio:
            x = ff.load_audio(src, SR)
            if overlay_on:
                log("transcribing")
                from .transcribe import DEFAULT_MODEL, transcribe
                tr = transcribe(x, SR, model or DEFAULT_MODEL, language)
                words = tr["words"]
                report["transcript"] = {k: v for k, v in tr.items() if k != "words"}
                report["transcript"]["text"] = " ".join(w.text for w in words)
            log("cleaning audio")
            hits = A.detect_aircraft(x, SR)
            rng = np.random.default_rng(seed)
            y, info_a = A.clean(x, SR, audio_mode, hits=hits, rng=rng)
            report["audio_chain"] = info_a["steps"]
            wav = job / "clean.wav"
            ff.write_wav(wav, y, SR)

        vf = []
        if prnu:
            # small upscale + centre crop and light denoise: shifts the sensor-noise grid
            # and smooths the pattern. weakens prnu matching; not a guarantee.
            vf.append(f"scale=trunc(iw*1.02/2)*2:trunc(ih*1.02/2)*2:flags=lanczos,"
                      f"crop={W}:{H},hqdn3d=1.5:1.5:3:3")
        else:
            vf.append(f"crop={W}:{H}")

        if overlay_on and words:
            log("placing transcript")
            if lowercase:
                for w in words:
                    w.text = w.text.lower()
            lay = overlay.layout(W, H, platform)
            lines = overlay.break_lines(words, lay)
            report["overlay"] = {"orientation": lay.orientation, "platform": lay.platform,
                                 "size": [W, H], "font_px": lay.font_px,
                                 "box": [round(lay.box_x), round(lay.y_mid - lay.line_h * 1.5),
                                         round(lay.box_w), round(lay.line_h * 3)],
                                 "lines": len(lines),
                                 "text_color": text_color,
                                 "colors": overlay.assign_colors(
                                     src, lines, lay, fixed_rgb=_parse_color(text_color))}
            overlay.write_ass(lines, lay, job / "lyrics.ass")
            fonts = job / "fonts"
            fonts.mkdir()
            for f in overlay.FONT_DIR.glob("*.otf"):
                shutil.copy(f, fonts / f.name)
            vf.append("subtitles=lyrics.ass:fontsdir=fonts")
        vf.append("format=yuv420p")

        log("rendering")
        args = [ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(src)]
        if wav:
            args += ["-i", str(wav)]
        args += ["-map", "0:v:0"]
        if wav:
            args += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
        args += [
            "-vf", ",".join(vf),
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-bsf:v", "filter_units=remove_types=6",      # drop sei (x264 settings string)
            "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn",
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-metadata:s:v:0", "handler_name=", "-metadata:s:a:0", "handler_name=",
            "-metadata:s:v:0", "encoder=", "-metadata:s:a:0", "encoder=",
            "-movflags", "+faststart", str(job / "out.mp4"),
        ]
        _run_in(args, job)
        if wav:
            n = _polish_audio(job, report)
            if n:
                report["audio_chain"].append(f"post-encode pass: {n} residual tone(s) removed")
        _blank_compressor_name(job / "out.mp4")
        shutil.move(str(job / "out.mp4"), out_path)

        log("verifying")
        report["after"] = scan(out_path)
        report["checks"] = checks(report)
        report["seconds"] = round(time.time() - t_start, 1)
        (out_dir / f"{stem}.report.json").write_text(json.dumps(report, indent=2, default=str))
        (out_dir / f"{stem}.report.md").write_text(render_md(report))
        return report
    finally:
        shutil.rmtree(job, ignore_errors=True)


def _parse_color(spec: str):
    """'auto' = match the scene; 'golden-brown' (default); or '#rrggbb'."""
    from .color import GOLDEN_BROWN
    if spec == "auto":
        return None
    if spec in ("golden-brown", "golden_brown"):
        return GOLDEN_BROWN
    h = spec.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"bad colour: {spec}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _polish_audio(job: Path, report: dict, passes: int = 2) -> int:
    """lossy aac can re-expose faint tone fragments. decode the encoded output, and if the
    detector still finds a doppler track, remove it and re-mux (video is copied)."""
    removed = 0
    for i in range(passes):
        out = job / "out.mp4"
        y = ff.load_audio(out, SR)
        hits = A.detect_aircraft(y, SR)
        if not hits:
            break
        removed += len(hits)
        y = A.lowpass(A.remove_tracks(y, SR, hits), SR)
        ff.write_wav(job / "polish.wav", y.astype(np.float32), SR)
        _run_in([ff.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", "out.mp4",
                 "-i", "polish.wav", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-map_chapters", "-1",
                 "-fflags", "+bitexact", "-flags:a", "+bitexact",
                 "-metadata:s:v:0", "handler_name=", "-metadata:s:a:0", "handler_name=",
                 "-metadata:s:v:0", "encoder=", "-metadata:s:a:0", "encoder=",
                 "-movflags", "+faststart", "out2.mp4"], job)
        (job / "out2.mp4").replace(out)
    return removed


def _blank_compressor_name(path: Path) -> None:
    """the mp4 muxer copies the encoder name ("Lavc libx264") into the 32-byte
    compressorname field of the sample description. blank it in place: the field is a
    fixed-size pascal string, so the file layout does not change."""
    # +faststart puts the moov box (which holds stsd) at the front of the file
    with path.open("r+b") as f:
        head = f.read(32 * 1024 * 1024)
        i = 0
        while (i := head.find(b"Lavc", i)) != -1:
            n = head[i - 1]
            if 0 < n < 32 and head[i:i + n].startswith(b"Lavc"):
                f.seek(i - 1)
                f.write(bytes(32))
            i += 4


def _run_in(args, cwd):
    import subprocess
    p = subprocess.run(args, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.returncode:
        raise ff.ToolError(p.stderr.decode("utf-8", "replace")[-2000:])


def checks(r: dict) -> list[dict]:
    after = r["after"]
    a = after.get("audio") or {}
    out = [
        {"check": "identifying metadata / fingerprints in output",
         "pass": len(after["metadata"]) == 0, "detail": after["metadata"]},
    ]
    if a:
        out += [
            {"check": "mains hum (enf) in output", "pass": not a["enf"]["detected"],
             "detail": a["enf"]},
            {"check": "ultrasonic tone in output", "pass": not a["ultrasonic"].get("detected"),
             "detail": a["ultrasonic"]},
            {"check": "aircraft doppler signatures in output", "pass": len(a["aircraft"]) == 0,
             "detail": a["aircraft"]},
        ]
    ov = r.get("overlay")
    if ov:
        worst = min(c["contrast"] for c in ov["colors"])
        out.append({"check": "transcript contrast >= 4.5:1 (wcag aa)", "pass": worst >= 4.5,
                    "detail": {"worst_line_contrast": worst}})
    return out


def render_md(r: dict) -> str:
    L = [f"# aureon.video report — {r['output']}", ""]
    b = r["before"]
    L.append(f"**found before cleaning:** {b['summary']['total']} identifying item(s)")
    L.append("")
    for f in b["metadata"]:
        L.append(f"- ~~{f['key']}: {f['value']}~~ removed")
    a = b.get("audio") or {}
    if a:
        if a["enf"]["detected"]:
            L.append(f"- ~~mains hum {a['enf']['grid_hz']} hz (snr {a['enf']['snr_db']} db)~~ removed")
        if a["ultrasonic"].get("detected"):
            L.append(f"- ~~ultrasonic tone {a['ultrasonic']['peak_hz']} hz~~ removed")
        for e in a["aircraft"]:
            L.append(f"- ~~aircraft fly-over at {e['t0']} s: {len(e['tones'])} tone(s), "
                     f"~{e['speed_ms']} m/s, closest ~{e['distance_m']} m~~ removed")
    L += ["", "## audio chain", ""] + [f"1. {s}" for s in r.get("audio_chain", [])]
    if r.get("transcript"):
        t = r["transcript"]
        L += ["", "## transcript", "", f"model `{t['model']}` on {t['device']}, "
              f"language `{t['language']}` ({t['language_prob']})", "", f"> {t['text']}"]
    L += ["", "## checks", ""]
    for c in r["checks"]:
        L.append(f"- {'PASS' if c['pass'] else 'FAIL'} — {c['check']}")
    L += ["", "note: the sensor noise fingerprint (prnu) is weakened, not guaranteed removed. "
          "what is visible in the picture (houses, signs, skyline) is not changed."]
    return "\n".join(L) + "\n"
