from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> None:
    _bootstrap()
    if len(sys.argv) == 1:
        sys.argv.append("start")
    from webcrawler.main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
