"""deterministic colour matching: oklab, k-means, wcag contrast. no ai."""
from __future__ import annotations

import numpy as np

TARGET_CONTRAST = 4.5
GOLDEN_BROWN = (153, 101, 21)   # #996515


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def linear_to_oklab(rgb):
    lms = np.cbrt(rgb @ _M1.T)
    return lms @ _M2.T


def oklab_to_linear(lab):
    lms = lab @ np.linalg.inv(_M2).T
    return (lms ** 3) @ np.linalg.inv(_M1).T


def luminance(linear_rgb):
    return linear_rgb @ np.array([0.2126, 0.7152, 0.0722])


def contrast(y1, y2):
    hi, lo = max(y1, y2), min(y1, y2)
    return (hi + 0.05) / (lo + 0.05)


def kmeans(x: np.ndarray, k: int = 3, iters: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """lloyd's k-means with a deterministic init (lightness quantiles)."""
    order = np.argsort(x[:, 0])
    cent = np.stack([x[order[int((i + 0.5) / k * (len(x) - 1))]] for i in range(k)])
    for _ in range(iters):
        d = ((x[:, None, :] - cent[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for i in range(k):
            if (lab == i).any():
                cent[i] = x[lab == i].mean(0)
    counts = np.bincount(lab, minlength=k)
    return cent, counts


def _in_gamut(lab):
    rgb = oklab_to_linear(lab)
    return np.all(rgb >= -1e-6) and np.all(rgb <= 1 + 1e-6)


def _make(L, a, b):
    """oklab colour with chroma reduced until it fits in srgb."""
    lo, hi = 0.0, 1.0
    if _in_gamut(np.array([L, a, b])):
        return np.array([L, a, b])
    for _ in range(20):
        mid = (lo + hi) / 2
        if _in_gamut(np.array([L, a * mid, b * mid])):
            lo = mid
        else:
            hi = mid
    return np.array([L, a * lo, b * lo])


def analyse(pixels_rgb8: np.ndarray) -> dict:
    """summarise the background behind the text box."""
    lin = srgb_to_linear(pixels_rgb8.reshape(-1, 3) / 255.0)
    lab = linear_to_oklab(lin)
    Y = luminance(lin)
    cent, counts = kmeans(lab, 3)
    w = counts / counts.sum()
    chroma = np.hypot(cent[:, 1], cent[:, 2])
    dom = int(np.argmax(counts))
    cand = [i for i in range(3) if w[i] >= 0.10]
    src = max(cand, key=lambda i: chroma[i])
    return {
        "dominant_lab": cent[dom], "hue_ab": cent[src, 1:], "chroma": float(chroma[src]),
        "y_p25": float(np.percentile(Y, 25)), "y_p75": float(np.percentile(Y, 75)),
        "y_mean": float(Y.mean()),
    }


def lab_of(rgb8_):
    return linear_to_oklab(srgb_to_linear(np.array(rgb8_, dtype=np.float64) / 255.0))


def pick_fixed(stats: dict, rgb8_=GOLDEN_BROWN) -> dict:
    """keep a brand colour's hue and chroma; only its lightness adapts to the scene."""
    lab = lab_of(rgb8_)
    return pick(stats, hue_ab=lab[1:], fixed=True)


def pick(stats: dict, hue_ab=None, prefer: str | None = None, fixed: bool = False) -> dict:
    """choose a text colour that shares the scene's hue and is as close to the
    background as it can be while still meeting the contrast target.

    light text is checked against the 75th-percentile background luminance and dark
    text against the 25th, so bright or dark patches behind the text are covered."""
    ab = np.asarray(hue_ab if hue_ab is not None else stats["hue_ab"], dtype=np.float64)
    c = float(np.hypot(*ab))
    if not fixed:
        tint = min(max(c * 0.6, 0.0), 0.08)
        ab = ab / c * tint if c > 1e-4 else np.zeros(2)
    target = TARGET_CONTRAST * 1.1  # margin for motion in the scene

    def search(direction):
        ref = stats["y_p75"] if direction == "light" else stats["y_p25"]
        Ls = np.arange(1.0, 0.0, -0.005) if direction == "light" else np.arange(0.0, 1.0, 0.005)
        best = None
        for L in Ls:
            lab = _make(L, *ab)
            y = float(luminance(np.clip(oklab_to_linear(lab), 0, 1)))
            cr = contrast(y, ref)
            if cr >= target:
                best = (lab, cr)
            else:
                break
        if best is None:
            lab = _make(Ls[0], *ab)
            y = float(luminance(np.clip(oklab_to_linear(lab), 0, 1)))
            return lab, contrast(y, ref), False
        return best[0], best[1], True

    light = search("light")
    dark = search("dark")
    if prefer is None:
        prefer = "light" if (fixed or stats["y_mean"] < 0.30) else "dark"
    first, second = (light, dark) if prefer == "light" else (dark, light)
    if first[2]:
        lab, cr, ok, mode = *first, prefer
    elif second[2]:
        lab, cr, ok, mode = *second, ("dark" if prefer == "light" else "light")
    else:
        # mid-tone or very busy background: no fill colour alone reaches the target, so
        # the text gets a dense halo and its contrast is measured against that halo.
        text_lab = _make(0.86 if fixed else 0.97, *(ab if fixed else ab * 0.5))
        halo_lab = _make(0.14, *(ab * 0.3))
        y_t = float(luminance(np.clip(oklab_to_linear(text_lab), 0, 1)))
        y_h = float(luminance(np.clip(oklab_to_linear(halo_lab), 0, 1)))
        return {"text": rgb8(text_lab), "outline": rgb8(halo_lab),
                "contrast": round(contrast(y_t, y_h), 2), "meets_target": False,
                "mode": "halo", "lab": text_lab}
    text = rgb8(lab)
    # soft outline in a deepened version of the background, for busy scenes
    dom = stats["dominant_lab"]
    oL = max(0.0, dom[0] - 0.25) if mode == "light" else min(1.0, dom[0] + 0.25)
    outline = rgb8(_make(oL, dom[1] * 0.5, dom[2] * 0.5))
    return {"text": text, "outline": outline, "contrast": round(cr, 2), "meets_target": ok,
            "mode": mode, "lab": lab}


def rgb_contrast(a_rgb8, b_rgb8) -> float:
    ya = float(luminance(srgb_to_linear(np.array(a_rgb8) / 255.0)))
    yb = float(luminance(srgb_to_linear(np.array(b_rgb8) / 255.0)))
    return contrast(ya, yb)


def rgb8(lab) -> tuple[int, int, int]:
    s = linear_to_srgb(np.clip(oklab_to_linear(np.asarray(lab)), 0, 1))
    return tuple(int(round(v * 255)) for v in s)


def text_contrast(text_rgb8, pixels_rgb8, pct) -> float:
    lin = srgb_to_linear(pixels_rgb8.reshape(-1, 3) / 255.0)
    y_ref = float(np.percentile(luminance(lin), pct))
    y_t = float(luminance(srgb_to_linear(np.array(text_rgb8) / 255.0)))
    return contrast(y_t, y_ref)
