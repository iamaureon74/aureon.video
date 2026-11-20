"""3-row synced transcript overlay (past / active / next) rendered as an ass file.

layout and colour are fully deterministic: geometry comes from the frame size,
colour from the pixels behind the text box (see color.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import ImageFont

from . import color, ff

FONT_DIR = Path(__file__).parent / "fonts"
FONT_FILE = FONT_DIR / "URWGothic-Book.otf"
FONT_NAME = "URW Gothic"
SCROLL_MS = 250
DIM_ALPHA = 0x70      # past / next rows (~55 % opaque)
UNSUNG_ALPHA = 0x60   # active row, words not yet spoken


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Line:
    words: list[Word]
    color: dict = field(default_factory=dict)

    @property
    def start(self):
        return self.words[0].start

    @property
    def end(self):
        return self.words[-1].end

    @property
    def text(self):
        return " ".join(w.text for w in self.words)


@dataclass
class Layout:
    width: int
    height: int
    orientation: str
    font_px: int
    line_h: float
    box_x: float
    box_w: float
    y_mid: float
    margin: float
    platform: str = "none"

    @property
    def rows(self):
        return self.y_mid - self.line_h, self.y_mid, self.y_mid + self.line_h

    def sample_region(self):
        """normalised (x0, y0, x1, y1) of the area the text sits on."""
        x0 = max(0.0, self.box_x - 0.02 * self.width) / self.width
        x1 = min(self.width, self.width - self.margin * 0.5) / self.width
        y0 = max(0.0, self.y_mid - 2 * self.line_h) / self.height
        y1 = min(self.height, self.y_mid + 2 * self.line_h) / self.height
        return x0, y0, x1, y1


# right-hand margin (fraction of width) that keeps text clear of each app's on-screen
# buttons. approximate, from the platforms' published safe-zone templates; the
# vertical apps put a like/comment/share column on the right edge at mid height.
PLATFORM_RIGHT_MARGIN = {
    "none": 0.045,
    "youtube": 0.045,
    "tiktok": 0.16,
    "reels": 0.15,
    "shorts": 0.17,
}


def resolve_platform(width: int, height: int, platform: str = "auto") -> str:
    if platform != "auto":
        if platform not in PLATFORM_RIGHT_MARGIN:
            raise ValueError(f"unknown platform {platform}")
        return platform
    # tall 9:16-ish video is almost always headed for tiktok / reels / shorts:
    # use the widest of their safe margins so one export works on all three
    return "shorts" if width / height <= 0.66 else "none"


def layout(width: int, height: int, platform: str = "auto") -> Layout:
    ratio = width / height
    if ratio >= 1.15:
        orient, frac = "landscape", 0.34
    elif ratio <= 0.87:
        orient, frac = "portrait", 0.46
    else:
        orient, frac = "square", 0.40
    plat = resolve_platform(width, height, platform)
    font_px = max(14, int(round(min(width, height) * 0.05)))
    margin = PLATFORM_RIGHT_MARGIN[plat] * width
    box_w = min(frac * width, width - margin - 0.04 * width)
    lay = Layout(width, height, orient, font_px, font_px * 1.45,
                 width - margin - box_w, box_w, height / 2, margin)
    lay.platform = plat
    return lay


def _font(px):
    return ImageFont.truetype(str(FONT_FILE), px)


def break_lines(words: list[Word], lay: Layout, max_words=8, max_gap=1.2) -> list[Line]:
    font = _font(lay.font_px)
    lines: list[Line] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            trial = " ".join(x.text for x in cur + [w])
            too_wide = font.getlength(trial) > lay.box_w
            gap = w.start - cur[-1].end > max_gap
            sentence = cur[-1].text.endswith((".", "?", "!")) and len(cur) >= 3
            if too_wide or gap or sentence or len(cur) >= max_words:
                lines.append(Line(cur))
                cur = []
        cur.append(w)
    if cur:
        lines.append(Line(cur))
    return lines


def assign_colors(video: Path, lines: list[Line], lay: Layout, ema=0.5,
                  fixed_rgb: tuple[int, int, int] | None = color.GOLDEN_BROWN) -> list[dict]:
    """sample the box region over each line's active window, pick a colour, smooth
    the hue over time, then re-check contrast per line so smoothing never breaks it."""
    if not lines:
        return []
    duration = lines[-1].end + 2
    fps = 4.0 if duration < 600 else 1.0
    x0, y0, x1, y1 = lay.sample_region()
    samples: list[tuple[float, np.ndarray]] = []
    for t, fr in ff.read_frames(video, fps, 480):
        h, w = fr.shape[:2]
        crop = fr[int(y0 * h):max(int(y0 * h) + 1, int(y1 * h)),
                  int(x0 * w):max(int(x0 * w) + 1, int(x1 * w))]
        flat = crop.reshape(-1, 3)
        step = max(1, len(flat) // 3000)
        samples.append((t, flat[::step]))
    if not samples:
        raise ff.ToolError("could not read video frames")
    times = np.array([s[0] for s in samples])
    starts = [ln.start for ln in lines] + [lines[-1].end + 1.5]
    picks = []
    hue = None
    for i, ln in enumerate(lines):
        a, b = starts[i] - 0.25, starts[i + 1]
        sel = [s[1] for s, t in zip(samples, times) if a <= t <= b]
        if not sel:
            sel = [samples[int(np.argmin(np.abs(times - ln.start)))][1]]
        px = np.concatenate(sel)
        stats = color.analyse(px)
        hue = stats["hue_ab"] if hue is None else ema * hue + (1 - ema) * stats["hue_ab"]
        p = color.pick_fixed(stats, fixed_rgb) if fixed_rgb else color.pick(stats, hue_ab=hue)
        if p["mode"] == "halo":
            p["measured_contrast"] = round(color.rgb_contrast(p["text"], p["outline"]), 2)
        else:
            pct = 75 if p["mode"] == "light" else 25
            p["measured_contrast"] = round(color.text_contrast(p["text"], px, pct), 2)
        ln.color = p
        picks.append({"line": i, "start": round(ln.start, 2), "text": p["text"],
                      "mode": p["mode"], "contrast": p["measured_contrast"],
                      "meets_target": p["meets_target"]})
    return picks


# ---------------------------------------------------------------- ass output

def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _bgr(rgb, alpha=0):
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _c(rgb):
    r, g, b = rgb
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _esc(s: str) -> str:
    return s.replace("\\", "⧵").replace("{", "(").replace("}", ")").replace("\n", " ")


def _karaoke(line: Line, event_start: float, glow_blur: float | None = None,
             rest_alpha: str = "&H80&", peak_alpha: str = "&H30&") -> str:
    """word-by-word fill (\\kf). with glow_blur set, each word also gets a short glow
    pulse the moment it is spoken: outline brightens and blooms for ~120 ms, then
    settles over ~380 ms. it never changes size, so the line never reflows."""
    parts = []
    lead = int(round((line.start - event_start) * 100))
    if lead > 0:
        parts.append(f"{{\\k{lead}}}")
    for j, w in enumerate(line.words):
        nxt = line.words[j + 1].start if j + 1 < len(line.words) else w.end
        dur = max(1, int(round((nxt - w.start) * 100)))
        sep = " " if j + 1 < len(line.words) else ""
        tags = f"\\kf{dur}"
        if glow_blur:
            t0 = max(0, int(round((w.start - event_start) * 1000)))
            tags += (f"\\t({t0},{t0 + 120},\\blur{glow_blur * 2.6:.1f}\\3a{peak_alpha})"
                     f"\\t({t0 + 120},{t0 + 500},\\blur{glow_blur:.1f}\\3a{rest_alpha})")
        parts.append(f"{{{tags}}}{_esc(w.text)}{sep}")
    return "".join(parts)


def write_ass(lines: list[Line], lay: Layout, path: Path, tail=1.5) -> None:
    W, H = lay.width, lay.height
    top, mid, bot = lay.rows
    x = int(round(lay.box_x))
    lh = lay.line_h
    bord = max(1.0, lay.font_px * 0.06)
    blur = max(1.0, lay.font_px * 0.12)
    sm = SCROLL_MS
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: void,{FONT_NAME},{lay.font_px},&H00FFFFFF,&H60FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,{lay.font_px * 0.02:.1f},0,1,{bord:.1f},0,4,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []

    def add(s, e, tags, text):
        if e - s >= 0.01:
            ev.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},void,,0,0,0,,{{{tags}}}{text}")

    n = len(lines)
    if not n:
        path.write_text(head, encoding="utf-8")
        return
    starts = [ln.start for ln in lines]
    end_all = lines[-1].end + tail

    for i, ln in enumerate(lines):
        s = starts[i]
        e = starts[i + 1] if i + 1 < n else end_all
        col = ln.color or {"text": (255, 255, 255), "outline": (0, 0, 0)}
        c1, c3 = _c(col["text"]), _c(col["outline"])
        halo = col.get("mode") == "halo"
        o_alpha = "&H20&" if halo else "&H80&"
        o_bord = f"\\bord{bord * 2.5:.1f}" if halo else ""
        base = f"\\1c{c1}\\2c{c1}\\3c{c3}\\3a{o_alpha}{o_bord}\\blur{blur:.1f}"
        last = i == n - 1
        fade_out = "\\fad(0,300)" if last else ""
        # active row: slides up from the bottom row, words fill as they are spoken
        add(s, e, f"{base}\\1a&H00&\\2a&H{UNSUNG_ALPHA:02X}&{fade_out}"
                  f"\\move({x},{bot:.0f},{x},{mid:.0f},0,{sm})",
            _karaoke(ln, s, blur, o_alpha, "&H00&" if halo else "&H30&"))
        # past row
        if i > 0:
            p = lines[i - 1]
            pc = p.color or col
            add(s, e, f"\\1c{_c(pc['text'])}\\3c{_c(pc['outline'])}\\3a&H80&\\blur{blur:.1f}"
                      f"\\1a&H00&\\t(0,{sm},\\1a&H{DIM_ALPHA:02X}&\\3a&HA0&){fade_out}"
                      f"\\move({x},{mid:.0f},{x},{top:.0f},0,{sm})", _esc(p.text))
        # line leaving through the top
        if i > 1:
            q = lines[i - 2]
            qc = q.color or col
            add(s, s + sm / 1000, f"\\1c{_c(qc['text'])}\\3c{_c(qc['outline'])}\\3a&HA0&"
                f"\\blur{blur:.1f}\\1a&H{DIM_ALPHA:02X}&\\t(0,{sm},\\1a&HFF&\\3a&HFF&)"
                f"\\move({x},{top:.0f},{x},{top - lh:.0f},0,{sm})", _esc(q.text))
        # next row: rises in from below
        if i + 1 < n:
            nx = lines[i + 1]
            add(s, e, f"{base}\\1a&HFF&\\3a&HFF&\\t(0,{sm},\\1a&H{DIM_ALPHA:02X}&\\3a&HA0&)"
                      f"\\move({x},{bot + lh:.0f},{x},{bot:.0f},0,{sm})", _esc(nx.text))

    # pre-roll: first line waits on the bottom row
    pre = max(0.0, starts[0] - 1.5)
    if starts[0] - pre > 0.05:
        c0 = lines[0].color or {"text": (255, 255, 255), "outline": (0, 0, 0)}
        add(pre, starts[0], f"\\1c{_c(c0['text'])}\\3c{_c(c0['outline'])}\\blur{blur:.1f}"
                            f"\\1a&H{DIM_ALPHA:02X}&\\3a&HA0&\\fad(300,0)\\pos({x},{bot:.0f})",
            _esc(lines[0].text))

    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
