import numpy as np
import pytest

from aureon import audio as A

from .fixture import flyover

SR = 48000


def tone(f, dur=10.0, amp=0.05):
    t = np.arange(int(dur * SR)) / SR
    return amp * np.sin(2 * np.pi * f * t)


def band_db(x, f, width=2.0):
    fr, p = A._psd(A.mono(x), SR, 0.5)
    sel = (fr > f - width) & (fr < f + width)
    return 10 * np.log10(p[sel].max() / 1) if False else float(np.max(p[sel]))


def test_enf_detected_and_removed():
    x = (tone(60, amp=0.02) + 0.001 * np.random.default_rng(0).standard_normal(10 * SR))[:, None]
    assert A.detect_enf(x, SR)["detected"]
    assert A.detect_enf(x, SR)["grid_hz"] == 60
    y, _ = A.clean(x, SR, hits=[])
    assert not A.detect_enf(y, SR)["detected"]
    assert band_db(x, 60) - band_db(y, 60) > 40


def test_enf_50hz_harmonic_only():
    x = (tone(100, amp=0.02) + 0.001 * np.random.default_rng(1).standard_normal(10 * SR))[:, None]
    r = A.detect_enf(x, SR)
    assert r["detected"] and r["grid_hz"] == 50


def test_no_false_enf_on_noise():
    x = 0.01 * np.random.default_rng(2).standard_normal((10 * SR, 1))
    assert not A.detect_enf(x, SR)["detected"]


def test_ultrasonic_detected_and_removed():
    rng = np.random.default_rng(3)
    x = (tone(19500, amp=0.01) + 0.002 * rng.standard_normal(10 * SR))[:, None]
    assert A.detect_ultrasonic(x, SR)["detected"]
    y, _ = A.clean(x, SR, hits=[])
    assert not A.detect_ultrasonic(y, SR)["detected"]


def test_flyover_detected_with_parameters():
    n = 14 * SR
    x = (flyover(n, SR, f0=110, speed=80, dist=250, t0=7)
         + 0.002 * np.random.default_rng(4).standard_normal(n))[:, None]
    hits = A.detect_aircraft(x, SR)
    assert hits, "fly-over not detected"
    ev = A.group_aircraft(hits)
    assert len(ev) == 1
    fund = min(hits, key=lambda h: h.f0)
    assert fund.f0 == pytest.approx(110, rel=0.05)
    assert fund.speed_ms == pytest.approx(80, rel=0.25)
    assert fund.t0 == pytest.approx(7, abs=0.6)


def test_flyover_removed():
    n = 14 * SR
    x = (flyover(n, SR, t0=7) + 0.002 * np.random.default_rng(5).standard_normal(n))[:, None]
    hits = A.detect_aircraft(x, SR)
    y, _ = A.clean(x, SR, hits=hits)
    assert A.detect_aircraft(y, SR) == []


def test_steady_tones_are_not_aircraft():
    rng = np.random.default_rng(6)
    x = (tone(220, 14) + tone(437, 14) + 0.002 * rng.standard_normal(14 * SR))[:, None]
    assert A.detect_aircraft(x, SR) == []


def test_rising_chirp_is_not_aircraft():
    t = np.arange(14 * SR) / SR
    f = 200 + 10 * t
    x = (0.05 * np.sin(2 * np.pi * np.cumsum(f) / SR))[:, None]
    assert A.detect_aircraft(x, SR) == []


def test_warp_keeps_sync():
    x = tone(440, 10)[:, None]
    y = A.warp(x, SR, rng=np.random.default_rng(7))
    # cross-correlate a click train to measure worst-case drift
    imp = np.zeros((10 * SR, 1))
    imp[:: SR // 2] = 1.0
    w = A.warp(imp, SR, rng=np.random.default_rng(7))
    peaks = np.where(w[:, 0] > 0.3)[0]
    drift = np.abs(peaks - np.round(peaks / (SR // 2)) * (SR // 2)).max() / SR
    assert drift <= 0.0055
    assert y.shape == x.shape


def test_clean_keeps_speech_band():
    x = tone(1234, 5, 0.3)[:, None]
    y, _ = A.clean(x, SR, hits=[])
    ratio = np.sqrt(np.mean(y[SR:-SR] ** 2) / np.mean(x[SR:-SR] ** 2))
    assert ratio > 0.8
