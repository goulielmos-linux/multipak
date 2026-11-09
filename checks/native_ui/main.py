#!/usr/bin/env python3
"""Launch interface previews and expose source-tree modules to native checks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from multipak.kirigami.main import (  # noqa: E402, F401
    PREVIEW_ENVIRONMENTS,
    Package,
    Welcome,
    create_preview,
    create_window,
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
