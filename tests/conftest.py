"""Shared test setup.

The backend is imported two ways across the codebase -- ``import database``
and ``from backend.backup_db import ...`` -- so both the repo root and the
backend directory have to be importable. Every test file was repeating
that; here it is once.

Note this is deliberately additive: it only appends to sys.path, so it
cannot change how any existing test resolves an import.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

for _path in (str(ROOT), str(BACKEND)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
