"""end to end: build a video full of threats, clean it, verify the output."""
import numpy as np
from PIL import Image

from aureon import meta, pipeline

from .fixture import build_video


def test_clean_end_to_end(tmp_path):
    img = tmp_path / "f.png"
    Image.fromarray((np.indices((360, 640)).sum(0) % 256).astype(np.uint8)).convert("RGB").save(img)
    src = build_video(img, tmp_path / "IMG_0001.mov", duration=12, size=(640, 360), with_speech=False)

    before = pipeline.scan(src)
    keys = {f["key"] for f in before["metadata"]}
    assert "location" in keys and "com.apple.quicktime.model" in keys
    assert before["audio"]["enf"]["detected"]
    assert before["audio"]["ultrasonic"]["detected"]
    assert before["audio"]["aircraft"]

    r = pipeline.clean(src, tmp_path / "out", overlay_on=False, log=lambda m: None)
    assert r["output"].startswith("void_")             # capture-time filename not reused
    assert all(c["pass"] for c in r["checks"]), r["checks"]
    out = tmp_path / "out" / r["output"]
    assert meta.scan(out)["findings"] == []
    assert b"x264 - core" not in out.read_bytes()
