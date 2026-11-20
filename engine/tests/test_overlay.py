import numpy as np
import pytest

from aureon import color, overlay
from aureon.overlay import Line, Word


@pytest.mark.parametrize("w,h,orient", [(1920, 1080, "landscape"), (1080, 1920, "portrait"),
                                        (1080, 1080, "square"), (334, 188, "landscape")])
def test_layout_right_middle(w, h, orient):
    lay = overlay.layout(w, h)
    assert lay.orientation == orient
    assert lay.box_x + lay.box_w / 2 > w / 2         # right side
    assert lay.box_x + lay.box_w < w                # inside frame
    top, mid, bot = lay.rows
    assert mid == h / 2 and top > 0 and bot < h     # vertically centred, 3 rows fit


def test_line_breaking_fits_box():
    lay = overlay.layout(1280, 720)
    words = [Word(t, i * 0.4, i * 0.4 + 0.3) for i, t in enumerate(
        "this is a fairly long sentence that should never overflow the box on the right".split())]
    lines = overlay.break_lines(words, lay)
    font = overlay._font(lay.font_px)
    assert len(lines) >= 2
    for ln in lines:
        assert font.getlength(ln.text) <= lay.box_w
    assert sum(len(ln.words) for ln in lines) == len(words)


def test_break_on_pause():
    lay = overlay.layout(1920, 1080)
    words = [Word("one", 0, 0.3), Word("two", 0.4, 0.7), Word("three", 3.0, 3.3)]
    lines = overlay.break_lines(words, lay)
    assert [ln.text for ln in lines] == ["one two", "three"]


@pytest.mark.parametrize("seed", range(25))
def test_color_pick_meets_contrast(seed):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, 3)
    px = np.clip(base + rng.normal(0, 18, (4000, 3)), 0, 255).astype(np.uint8)
    p = color.pick(color.analyse(px))
    if p["mode"] == "halo":
        assert color.rgb_contrast(p["text"], p["outline"]) >= 4.5
    else:
        pct = 75 if p["mode"] == "light" else 25
        assert color.text_contrast(p["text"], px, pct) >= 4.5


def test_color_takes_scene_hue():
    px = np.tile(np.array([[20, 30, 90]], np.uint8), (3000, 1))     # deep blue scene
    p = color.pick(color.analyse(px))
    r, g, b = p["text"]
    assert p["mode"] == "light" and b > r                            # light, bluish text


def test_oklab_roundtrip():
    rgb = np.random.default_rng(0).random((100, 3))
    back = color.oklab_to_linear(color.linear_to_oklab(rgb))
    assert np.allclose(rgb, back, atol=1e-6)


def test_ass_has_three_rows_and_karaoke(tmp_path):
    lay = overlay.layout(1280, 720)
    words = [Word(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(
        "one two three. four five six. seven eight nine. ten eleven twelve.".split())]
    lines = overlay.break_lines(words, lay)
    for ln in lines:
        ln.color = {"text": (230, 220, 200), "outline": (10, 10, 10)}
    p = tmp_path / "x.ass"
    overlay.write_ass(lines, lay, p)
    s = p.read_text()
    top, mid, bot = lay.rows
    assert "\\kf" in s
    assert f",{mid:.0f},0,250)" in s and f",{top:.0f},0,250)" in s and f",{bot:.0f},0,250)" in s
    assert "Fontname" in s and "URW Gothic" in s


def test_ass_escapes_override_braces(tmp_path):
    lay = overlay.layout(1280, 720)
    lines = [Line([Word("{\\pos(0,0)}hack", 0, 1)], {"text": (1, 1, 1), "outline": (0, 0, 0)})]
    p = tmp_path / "x.ass"
    overlay.write_ass(lines, lay, p)
    assert "{\\pos(0,0)}" not in p.read_text()


@pytest.mark.parametrize("seed", range(25))
def test_golden_brown_keeps_hue_and_contrast(seed):
    rng = np.random.default_rng(100 + seed)
    base = rng.integers(0, 256, 3)
    px = np.clip(base + rng.normal(0, 18, (4000, 3)), 0, 255).astype(np.uint8)
    p = color.pick_fixed(color.analyse(px))
    r, g, b = p["text"]
    assert r > g > b                                     # warm gold/brown hue kept
    if p["mode"] == "halo":
        assert color.rgb_contrast(p["text"], p["outline"]) >= 4.5
    else:
        pct = 75 if p["mode"] == "light" else 25
        assert color.text_contrast(p["text"], px, pct) >= 4.5


SIZES = {
    "tiktok / reels / shorts 1080x1920": (1080, 1920),
    "720x1280 vertical": (720, 1280),
    "instagram feed 4:5 1080x1350": (1080, 1350),
    "instagram square 1080x1080": (1080, 1080),
    "youtube 1920x1080": (1920, 1080),
    "youtube 4k 3840x2160": (3840, 2160),
    "small 334x188": (334, 188),
    "ultrawide 2560x1080": (2560, 1080),
}


@pytest.mark.parametrize("name", SIZES)
def test_every_size_fits(name):
    w, h = SIZES[name]
    lay = overlay.layout(w, h)
    top, mid, bot = lay.rows
    assert lay.box_x > 0 and lay.box_x + lay.box_w <= w - lay.margin + 1e-6
    assert top - lay.line_h / 2 > 0 and bot + lay.line_h / 2 < h
    assert lay.box_w >= lay.font_px * 6            # room for a few words per row
    words = [Word("possibility", i, i + 0.5) for i in range(12)]
    font = overlay._font(lay.font_px)
    for ln in overlay.break_lines(words, lay):
        assert font.getlength(ln.text) <= lay.box_w or len(ln.words) == 1


def test_vertical_video_avoids_app_buttons():
    lay = overlay.layout(1080, 1920)
    assert lay.platform == "shorts"
    assert lay.box_x + lay.box_w <= 1080 * (1 - 0.17) + 1e-6


def test_explicit_platform():
    assert overlay.layout(1080, 1920, "tiktok").platform == "tiktok"
    assert overlay.layout(1920, 1080).platform == "none"
