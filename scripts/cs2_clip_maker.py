#!/usr/bin/env python3
"""CLI entrypoint wrapper for CS2 Clip Maker Agent Skill."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
sys.path = [_SRC_DIR] + [p for p in sys.path if p not in (_SCRIPT_DIR, _SRC_DIR)]

from cs2_clip_maker.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
