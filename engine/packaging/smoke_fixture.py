"""make a small test video (no speech) for the windows smoke test."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixture import build_video  # noqa: E402

out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
img = out.parent / "frame.png"
from PIL import Image  # noqa: E402

Image.fromarray((np.indices((360, 640)).sum(0) % 256).astype(np.uint8)).convert("RGB").save(img)
build_video(img, out, duration=12, size=(640, 360), with_speech=False)
print(out)
