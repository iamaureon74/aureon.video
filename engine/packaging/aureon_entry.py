"""pyinstaller entry point: double-click opens the interface; arguments run the cli."""
import sys

from aureon.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["app"]))
