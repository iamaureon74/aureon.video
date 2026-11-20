"""local speech-to-text with word timestamps (faster-whisper / ctranslate2)."""
from __future__ import annotations

import os

import numpy as np
from scipy import signal

from .overlay import Word

DEFAULT_MODEL = os.environ.get("AUREON_MODEL", "large-v3-turbo")


def _device():
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def transcribe(audio: np.ndarray, sr: int, model: str = DEFAULT_MODEL,
               language: str | None = None) -> dict:
    from faster_whisper import WhisperModel

    m = audio.mean(axis=1) if audio.ndim == 2 else audio
    m16 = signal.resample_poly(m, 1, sr // 16000) if sr % 16000 == 0 else \
        signal.resample(m, int(len(m) * 16000 / sr))
    device, ctype = _device()
    wm = WhisperModel(model, device=device, compute_type=ctype)
    segments, info = wm.transcribe(
        m16.astype(np.float32), language=language, word_timestamps=True,
        vad_filter=True, beam_size=5, condition_on_previous_text=False,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append(Word(t, float(w.start), float(w.end)))
    return {"words": words, "language": info.language,
            "language_prob": round(float(info.language_probability), 3),
            "model": model, "device": device}
