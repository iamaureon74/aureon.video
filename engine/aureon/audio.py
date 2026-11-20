"""hidden-signal detection and removal. classical dsp only (numpy / scipy).

signals handled:
- enf: mains hum at 50/60 hz and harmonics (power-grid timestamp / location matching)
- ultrasonic beacons: narrowband tones 17-24 khz (cross-device ad tracking)
- infrasound: < 20 hz
- aircraft doppler signatures: narrowband engine/propeller tones whose pitch
  follows a fly-over doppler curve (matched against ads-b flight logs)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
from scipy import optimize, signal

SPEED_OF_SOUND = 343.0


# ---------------------------------------------------------------- helpers

def mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim == 2 else x


def _psd(x: np.ndarray, sr: int, res_hz: float):
    nper = int(min(len(x), max(256, sr / res_hz)))
    f, p = signal.welch(x, sr, nperseg=nper, noverlap=nper // 2)
    return f, 10 * np.log10(p + 1e-20)


def _peak_snr(f, pdb, lo, hi, ref_lo, ref_hi):
    """prominence (db) of the strongest true peak inside [lo, hi]. prominence is measured
    against the higher of the two surrounding bases, so a filter slope or notch edge
    never counts as a tone."""
    win = (f >= ref_lo) & (f <= ref_hi)
    idx = np.where(win)[0]
    if len(idx) < 5:
        return -99.0, 0.0
    seg = pdb[idx]
    pk, props = signal.find_peaks(seg, prominence=0, wlen=len(seg))
    best, bf = -99.0, 0.0
    for p_, pr in zip(pk, props["prominences"]):
        fp = f[idx[p_]]
        if lo <= fp <= hi and pr > best:
            best, bf = float(pr), float(fp)
    return best, bf


# ---------------------------------------------------------------- detectors

def detect_enf(x: np.ndarray, sr: int) -> dict:
    """look for 50 or 60 hz hum (fundamental or first harmonics)."""
    m = mono(x)
    f, pdb = _psd(m, sr, 0.5)
    best = {"detected": False, "grid_hz": None, "snr_db": 0.0, "peak_hz": None}
    floor = np.median(pdb[(f >= 100) & (f <= 4000)])
    for grid in (50, 60):
        for k in (1, 2, 3):
            c = grid * k
            # grid frequency stays within ~±0.2 hz of nominal; harmonics scale with k
            snr, pk = _peak_snr(f, pdb, c - 0.4 * k, c + 0.4 * k, c - 8 * k, c + 8 * k)
            # 20 db under the recording's own noise floor is treated as unrecoverable
            if pk and pdb[np.searchsorted(f, pk)] < floor - 20:
                continue
            if snr > best["snr_db"]:
                best = {"detected": snr >= 12.0, "grid_hz": grid, "snr_db": round(snr, 1),
                        "peak_hz": round(pk, 2)}
    if not best["detected"]:
        best["grid_hz"] = None
    return best


def detect_ultrasonic(x: np.ndarray, sr: int) -> dict:
    if sr < 36000:
        return {"detected": False, "checked": False, "reason": f"sample rate {sr} hz"}
    m = mono(x)
    f, pdb = _psd(m, sr, 5.0)
    hi = min(24000, sr / 2 - 200)
    band = (f >= 17000) & (f <= hi)
    best, bf = -99.0, 0.0
    idx = np.where(band)[0]
    pk, props = signal.find_peaks(pdb[idx], prominence=0, wlen=81)
    if len(pk):
        widths = signal.peak_widths(pdb[idx], pk, rel_height=0.5,
                                    prominence_data=(props["prominences"], props["left_bases"],
                                                     props["right_bases"]))[0]
    else:
        widths = []
    df = f[1] - f[0]
    for p_, pr, w in zip(pk, props["prominences"], widths):
        # a beacon is a pure tone: a few hz wide. a filter / codec roll-off edge is a
        # broad shelf and must not count.
        if w * df > 40.0:
            continue
        if pr > best:
            best, bf = float(pr), float(f[idx[p_]])
    speech = (f >= 300) & (f <= 4000)
    level = float(pdb[np.searchsorted(f, bf)] - np.median(pdb[speech])) if bf else -99.0
    audible = level > -70.0
    return {"detected": best >= 15.0 and audible, "checked": True, "peak_hz": round(bf, 1),
            "snr_db": round(best, 1), "level_vs_speech_db": round(level, 1)}


@dataclass
class Doppler:
    f0: float          # rest frequency of the tone (hz)
    beta: float        # v / c
    t0: float          # time of closest approach (s, reception time)
    tau: float         # closest distance / speed (s)
    start: float       # detected track span
    end: float
    r2: float

    @property
    def speed_ms(self) -> float:
        return self.beta * SPEED_OF_SOUND

    @property
    def distance_m(self) -> float:
        return self.speed_ms * self.tau

    def freq(self, t: np.ndarray) -> np.ndarray:
        return doppler_curve(t, self.f0, self.beta, self.t0, self.tau)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(speed_ms=round(self.speed_ms, 1), distance_m=round(self.distance_m, 0))
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}


def doppler_curve(t, f0, beta, t0, tau):
    """received frequency of a source passing in a straight line at constant speed."""
    u = (t - t0) / tau
    return f0 / (1.0 + beta * u / np.sqrt(u * u + 1.0))


def _track_ridges(S_db, freqs, times, fmin, fmax, min_len_s, max_gap):
    """greedy frame-to-frame peak linking. returns list of (t_idx[], f[])."""
    band = (freqs >= fmin) & (freqs <= fmax)
    idx = np.where(band)[0]
    base = signal.medfilt(S_db[idx, :], kernel_size=(31, 1)) if len(idx) > 31 else S_db[idx, :]
    white = S_db[idx, :] - base
    df = freqs[1] - freqs[0]
    hop = times[1] - times[0] if len(times) > 1 else 1.0
    active: list[dict] = []
    done: list[dict] = []
    for j in range(white.shape[1]):
        col = white[:, j]
        pk, props = signal.find_peaks(col, height=9.0, distance=3)
        if len(pk) > 10:
            pk = pk[np.argsort(props["peak_heights"])[-10:]]
        # parabolic interpolation for sub-bin frequency
        fs = []
        for p in pk:
            if 0 < p < len(col) - 1:
                a, b, c = col[p - 1], col[p], col[p + 1]
                den = a - 2 * b + c
                off = 0.5 * (a - c) / den if den != 0 else 0.0
            else:
                off = 0.0
            fs.append(freqs[idx[p]] + off * df)
        used = set()
        for tr in active:
            pred = tr["f"][-1]
            if len(tr["f"]) >= 3:
                pred += (tr["f"][-1] - tr["f"][-3]) / 2
            tol = max(2.5 * df, 0.02 * pred)
            best, bd = None, tol
            for k, fk in enumerate(fs):
                if k not in used and abs(fk - pred) <= bd:
                    best, bd = k, abs(fk - pred)
            if best is not None:
                used.add(best)
                tr["t"].append(j)
                tr["f"].append(fs[best])
                tr["miss"] = 0
            else:
                tr["miss"] += 1
        still = []
        for tr in active:
            (done if tr["miss"] > max_gap else still).append(tr)
        active = still
        for k, fk in enumerate(fs):
            if k not in used:
                active.append({"t": [j], "f": [fk], "miss": 0})
    done += active
    min_frames = int(min_len_s / hop)
    return [(np.array(t["t"]), np.array(t["f"])) for t in done if len(t["t"]) >= min_frames]


def _fit_doppler(t, f):
    fmax, fmin = np.percentile(f, 95), np.percentile(f, 5)
    if fmin <= 0 or fmax / fmin < 1.03:
        return None
    # pitch must fall over time: an approaching source sounds higher than a receding one
    if np.corrcoef(t, f)[0, 1] > -0.7:
        return None
    r = fmax / fmin
    beta0 = min(0.55, (r - 1) / (r + 1))
    f0_0 = 2 * fmax * fmin / (fmax + fmin)
    slope = np.gradient(signal.savgol_filter(f, min(len(f) // 2 * 2 - 1, 31), 2), t)
    t0_0 = t[np.argmin(slope)]
    tau0 = max(0.3, (t[-1] - t[0]) / 6)
    try:
        p, _ = optimize.curve_fit(
            doppler_curve, t, f, p0=[f0_0, beta0, t0_0, tau0],
            bounds=([fmin * 0.5, 0.005, t[0] - 30, 0.05], [fmax * 2, 0.7, t[-1] + 30, 120]),
            maxfev=4000,
        )
    except (RuntimeError, ValueError):
        return None
    pred = doppler_curve(t, *p)
    ss_res = float(np.sum((f - pred) ** 2))
    ss_tot = float(np.sum((f - f.mean()) ** 2)) or 1e-9
    r2 = 1 - ss_res / ss_tot
    rms_rel = math.sqrt(ss_res / len(f)) / p[0]
    if r2 < 0.98 or rms_rel > 0.015:
        return None
    return p, r2


def detect_aircraft(x: np.ndarray, sr: int, fmin=40.0, fmax=4000.0, min_len_s=3.0) -> list[Doppler]:
    """find narrowband tones whose pitch traces a fly-over doppler curve."""
    m = mono(x)
    dec = max(1, int(sr // 12000))
    if dec > 1:
        m = signal.resample_poly(m, 1, dec)
    srd = sr / dec
    nper, hop = 4096, 512
    if len(m) < nper * 2:
        return []
    freqs, times, Z = signal.stft(m, srd, nperseg=nper, noverlap=nper - hop, boundary=None)
    S_db = 20 * np.log10(np.abs(Z) + 1e-12)
    hits: list[Doppler] = []
    for ti, f in _track_ridges(S_db, freqs, times, fmin, fmax, min_len_s, max_gap=3):
        t = times[ti]
        fit = _fit_doppler(t, f)
        if fit is None:
            continue
        (f0, beta, t0, tau), r2 = fit
        hits.append(Doppler(float(f0), float(beta), float(t0), float(tau),
                            float(t[0]), float(t[-1]), float(r2)))
    return _dedupe(hits)


def _dedupe(hits: list[Doppler]) -> list[Doppler]:
    """keep one entry per track; harmonics of the same aircraft are kept (each needs a notch)."""
    out: list[Doppler] = []
    for h in sorted(hits, key=lambda h: -h.r2):
        if not any(abs(h.f0 - o.f0) / o.f0 < 0.02 and abs(h.t0 - o.t0) < 1.0 for o in out):
            out.append(h)
    return sorted(out, key=lambda h: (h.t0, h.f0))


def _same_flyover(h: Doppler, fund: Doppler) -> bool:
    """true when h is (close to) an integer harmonic of fund over h's own span,
    or shares its closest-approach time."""
    if abs(h.t0 - fund.t0) < 1.5:
        return True
    t = np.linspace(h.start, h.end, 16)
    ratio = h.freq(t) / fund.freq(t)
    k = np.round(np.median(ratio))
    return k >= 1 and np.all(np.abs(ratio - k) / k < 0.04)


def _group(hits: list[Doppler]) -> list[list[Doppler]]:
    events: list[list[Doppler]] = []
    # strongest fits first so each event is anchored on a reliable curve
    for h in sorted(hits, key=lambda h: (-(h.end - h.start), -h.r2)):
        for ev in events:
            if any(_same_flyover(h, m) for m in ev):
                ev.append(h)
                break
        else:
            events.append([h])
    return sorted(events, key=lambda ev: ev[0].t0)


def group_aircraft(hits: list[Doppler]) -> list[dict]:
    """group tracks into fly-over events (harmonics of one aircraft = one event)."""
    out = []
    for ev in _group(hits):
        anchor = ev[0]
        out.append({"t0": round(anchor.t0, 2), "speed_ms": round(anchor.speed_ms, 1),
                    "distance_m": round(anchor.distance_m, 0),
                    "tones": [h.to_dict() for h in sorted(ev, key=lambda h: h.f0)]})
    return out


def scan(x: np.ndarray, sr: int) -> dict:
    hits = detect_aircraft(x, sr)
    return {
        "enf": detect_enf(x, sr),
        "ultrasonic": detect_ultrasonic(x, sr),
        "aircraft": group_aircraft(hits),
        "_aircraft_raw": hits,
    }


# ---------------------------------------------------------------- filters

def highpass(x, sr, fc=80.0, order=8):
    sos = signal.butter(order, fc, "highpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x, axis=0)


def lowpass(x, sr, fc=16000.0, order=12):
    if fc >= sr / 2 * 0.98:
        return x
    sos = signal.butter(order, fc, "lowpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x, axis=0)


def bandpass(x, sr, lo=110.0, hi=7500.0, order=8):
    sos = signal.butter(order, [lo, hi], "bandpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x, axis=0)


def enf_comb(x, sr, grids=(50, 60), max_hz=1000.0, q=30.0):
    """notch every mains harmonic of both grids. applied whether or not hum was
    detected: weak hum below the detection threshold can still be extracted."""
    y = x
    freqs = sorted({g * k for g in grids for k in range(1, int(max_hz // g) + 1)})
    for f in freqs:
        b, a = signal.iirnotch(f, q * (1 + f / 400.0), fs=sr)
        sos = signal.tf2sos(b, a)
        # cascaded twice and run forward-backward: ~4x the attenuation of a single notch
        y = signal.sosfiltfilt(np.vstack([sos, sos]), y, axis=0)
    return y


def _notch_curves(hits: list[Doppler], fmax=6000.0):
    """every curve to notch: each detected tone, plus integer multiples of the lowest
    tone of each fly-over (harmonics too weak to detect can still be matched)."""
    curves = list(hits)
    for ev in _group(hits):
        fund = ev[0]
        fund = min((h for h in ev if abs(h.t0 - fund.t0) < 1.5), key=lambda h: h.f0)
        # the true fundamental may be below the lowest detected tone: also cover
        # sub-multiples down to 40 hz, then every multiple of each candidate
        bases = [fund.f0 / d for d in range(1, 5) if fund.f0 / d >= 40.0]
        seen = {round(h.f0, 1) for h in curves}
        for b in bases:
            for k in range(1, int(fmax // b) + 1):
                f = b * k
                if round(f, 1) not in seen:
                    seen.add(round(f, 1))
                    curves.append(Doppler(f, fund.beta, fund.t0, fund.tau,
                                          fund.start, fund.end, fund.r2))
    return curves


def remove_tracks(x, sr, hits: list[Doppler], fmax=6000.0):
    """follow each detected doppler curve (and its harmonics) over the whole recording
    and clamp those bins down to the local noise floor. the tone disappears without
    leaving a hole that would itself trace the curve."""
    if not hits:
        return x
    from scipy import ndimage

    curves = _notch_curves(hits, fmax)
    nper, hop = 8192, 1024
    chans = x.shape[1] if x.ndim == 2 else 1
    xx = x.reshape(len(x), chans)
    out = np.empty_like(xx)
    for c in range(chans):
        # blackman-harris: -92 db side lobes, so a strong tone does not leak outside the mask
        win = "blackmanharris"
        freqs, times, Z = signal.stft(xx[:, c], sr, window=win, nperseg=nper,
                                      noverlap=nper - hop)
        df = freqs[1] - freqs[0]
        top = int(np.searchsorted(freqs, fmax * 1.05)) + 1
        mag = np.abs(Z[:top])
        floor = ndimage.median_filter(mag, size=(41, 1), mode="nearest")
        mask = np.zeros(mag.shape, dtype=bool)
        for h in curves:
            fk = h.freq(times)
            w = np.maximum(5 * df, 0.02 * fk)
            lo = np.searchsorted(freqs, fk - w)
            hi = np.searchsorted(freqs, fk + w)
            for ci in range(len(times)):
                if lo[ci] < top:
                    mask[lo[ci]:min(hi[ci], top), ci] = True
        # masked bins become noise at the local floor level: magnitude clamped and phase
        # randomised, so no coherent trace of the tone survives overlap-add.
        hit = mask & (mag > floor * 0.5)
        rng = np.random.default_rng()
        phase = np.exp(2j * np.pi * rng.random(int(hit.sum())))
        sub = Z[:top]
        sub[hit] = np.minimum(mag[hit], floor[hit]) * phase
        Z[:top] = sub
        _, y = signal.istft(Z, sr, window=win, nperseg=nper, noverlap=nper - hop)
        out[:, c] = y[: len(x)] if len(y) >= len(x) else np.pad(y, (0, len(x) - len(y)))
    return out.reshape(x.shape)


def warp(x, sr, max_offset_ms=5.0, rng: np.random.Generator | None = None):
    """bounded random time-offset modulation. pitch wobbles by ~1-2 % so a doppler
    curve no longer fits cleanly, while a/v sync never drifts by more than a few ms."""
    rng = rng or np.random.default_rng()
    n = len(x)
    t = np.arange(n) / sr
    off = np.zeros(n)
    for _ in range(4):
        period = rng.uniform(1.5, 4.0)
        off += np.sin(2 * np.pi * t / period + rng.uniform(0, 2 * np.pi))
    off *= (max_offset_ms / 1000.0) / 4.0 * 2.0
    off = np.clip(off, -max_offset_ms / 1000, max_offset_ms / 1000)
    src = np.clip(t + off, 0, t[-1])
    if x.ndim == 1:
        return np.interp(src, t, x)
    return np.stack([np.interp(src, t, x[:, c]) for c in range(x.shape[1])], axis=1)


def speech_gate(x, sr, segments: list[tuple[float, float]], pad=0.15, fade=0.05, floor_db=-60.0):
    """keep audio only inside speech segments (s), silence the rest."""
    n = len(x)
    env = np.full(n, 10 ** (floor_db / 20), dtype=np.float64)
    ramp = max(1, int(fade * sr))
    for s, e in segments:
        a, b = max(0, int((s - pad) * sr)), min(n, int((e + pad) * sr))
        env[a:b] = 1.0
    env = np.convolve(env, np.ones(ramp) / ramp, mode="same")
    return x * (env[:, None] if x.ndim == 2 else env)


def speech_segments(x, sr) -> list[tuple[float, float]]:
    """silero vad (ships with faster-whisper) at 16 khz."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    m = mono(x)
    m16 = signal.resample_poly(m, 1, sr // 16000) if sr % 16000 == 0 else \
        signal.resample(m, int(len(m) * 16000 / sr))
    ts = get_speech_timestamps(m16.astype(np.float32), VadOptions(min_silence_duration_ms=300))
    return [(d["start"] / 16000, d["end"] / 16000) for d in ts]


def clean(x: np.ndarray, sr: int, mode: str = "standard", hits=None,
          rng: np.random.Generator | None = None) -> tuple[np.ndarray, dict]:
    """full filter chain. modes: standard | warp | voice."""
    steps = []
    if hits is None:
        hits = detect_aircraft(x, sr)
    y = highpass(x, sr)
    steps.append("high-pass 80 hz (infrasound + mains fundamental)")
    y = enf_comb(y, sr)
    steps.append("notch comb at every 50/60 hz harmonic to 1 khz")
    if hits:
        y = remove_tracks(y, sr, hits)
        steps.append(f"tracking notch on {len(hits)} aircraft doppler tone(s) + harmonics")
        # re-scan and repeat: removing a strong tone can expose a weaker one
        for _ in range(2):
            more = detect_aircraft(y, sr)
            if not more:
                break
            y = remove_tracks(y, sr, more)
            steps.append(f"second pass: {len(more)} more doppler tone(s) removed")
    if mode == "warp":
        y = warp(y, sr, rng=rng)
        steps.append("random bounded time-warp (±5 ms, ~1-2 % pitch wobble)")
    if mode == "voice":
        segs = speech_segments(y, sr)
        y = bandpass(y, sr)
        y = speech_gate(y, sr, segs)
        steps.append(f"voice-only: band-pass 110-7500 hz, gated to {len(segs)} speech segment(s)")
    y = lowpass(y, sr)
    steps.append("low-pass 16 khz (ultrasonic beacons)")
    peak = float(np.max(np.abs(y))) or 1.0
    if peak > 0.98:
        y = y * (0.98 / peak)
    return y.astype(np.float32), {"mode": mode, "steps": steps}
