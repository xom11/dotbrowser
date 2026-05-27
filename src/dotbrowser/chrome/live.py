"""Live apply support for a running Google Chrome instance."""
from __future__ import annotations

from pathlib import Path

from dotbrowser._base import chromium_live as _shared
from dotbrowser._base.utils import Plan


def apply_live(port: int, prefs_path: Path, prefs: dict, plans: list[Plan]) -> None:
    _shared.apply_live(
        "Chrome", "chrome://settings/appearance", port, prefs_path, prefs, plans
    )
