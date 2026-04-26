"""Brave-browser-specific subcommands."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotbrowser.brave import shortcuts as shortcuts_mod


def _default_profile_root() -> Path | None:
    """Brave's profile root, per platform.

    Returns None for unsupported platforms; the CLI then requires
    --profile-root to be passed explicitly so that --help still works
    on Windows / BSD / etc. without crashing at import time.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("linux"):
        return home / ".config" / "BraveSoftware" / "Brave-Browser"
    return None


DEFAULT_PROFILE_ROOT = _default_profile_root()


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("brave", help="Brave browser commands")
    if DEFAULT_PROFILE_ROOT is not None:
        p.add_argument(
            "--profile-root",
            type=Path,
            default=DEFAULT_PROFILE_ROOT,
            help=f"default: {DEFAULT_PROFILE_ROOT}",
        )
    else:
        p.add_argument(
            "--profile-root",
            type=Path,
            required=True,
            help=f"required (no default for platform {sys.platform!r})",
        )
    p.add_argument(
        "--profile",
        default="Default",
        help="profile dir name (default: Default)",
    )
    sub = p.add_subparsers(dest="module", required=True, metavar="MODULE")
    shortcuts_mod.register(sub)
