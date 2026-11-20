"""aureon command line.

  aureon scan  video.mp4
  aureon clean video.mp4 -o out/ [--audio standard|warp|voice] [--no-overlay]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, pipeline


def _utf8_console():
    """windows consoles default to cp1252; never crash on a character it can't show."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    _utf8_console()
    p = argparse.ArgumentParser(prog="aureon", description="a little less noise.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="report what could identify a video (changes nothing)")
    s.add_argument("video")
    s.add_argument("--json", action="store_true")

    c = sub.add_parser("clean", help="strip, clean audio, add transcript, verify")
    c.add_argument("video")
    c.add_argument("-o", "--out", default=".")
    c.add_argument("--audio", choices=["standard", "warp", "voice"], default="standard")
    c.add_argument("--no-overlay", action="store_true")
    c.add_argument("--model", default=None, help="whisper model (default large-v3-turbo)")
    c.add_argument("--language", default=None)
    c.add_argument("--lowercase", action="store_true")
    c.add_argument("--color", default="golden-brown",
                   help="transcript colour: golden-brown (default), auto (match scene), #rrggbb")
    c.add_argument("--platform", default="auto",
                   choices=["auto", "none", "youtube", "tiktok", "reels", "shorts"],
                   help="keep text clear of that app's buttons (auto: vertical video -> shorts/tiktok/reels safe)")
    c.add_argument("--no-prnu", action="store_true")
    c.add_argument("--keep-name", action="store_true")
    ap = sub.add_parser("app", help="open the drag-and-drop interface in your browser")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    a = p.parse_args(argv)

    if a.cmd == "app":
        from .app import serve
        serve(a.port, open_browser=not a.no_browser)
        return 0

    if a.cmd == "scan":
        r = pipeline.scan(a.video)
        if a.json:
            print(json.dumps(r, indent=2, default=str))
        else:
            print(f"{r['summary']['total']} thing(s) could identify this video.")
            for f in r["metadata"]:
                print(f"  - {f['key']}: {f['value']}")
            au = r.get("audio") or {}
            if au.get("enf", {}).get("detected"):
                print(f"  - mains hum {au['enf']['grid_hz']} hz")
            if au.get("ultrasonic", {}).get("detected"):
                print(f"  - ultrasonic tone {au['ultrasonic']['peak_hz']} hz")
            for e in au.get("aircraft", []):
                print(f"  - aircraft doppler signature at {e['t0']} s")
        return 0

    r = pipeline.clean(
        a.video, a.out, audio_mode=a.audio, overlay_on=not a.no_overlay, model=a.model,
        language=a.language, prnu=not a.no_prnu, keep_name=a.keep_name, lowercase=a.lowercase,
        text_color=a.color, platform=a.platform,
        log=lambda m: print(f"- {m}", file=sys.stderr),
    )
    ok = all(c["pass"] for c in r["checks"])
    for c in r["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['check']}")
    print(f"\n-> {r['output']}  ({r['seconds']} s)")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
