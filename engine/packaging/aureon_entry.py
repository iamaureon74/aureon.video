"""pyinstaller entry point: double-click opens the interface; arguments run the cli."""
import os
import sys

# windowed (no-console) build: there is no stdout/stderr. give libraries that write
# progress bars (model download) a sink instead of letting them crash.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from aureon.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["app"]))
