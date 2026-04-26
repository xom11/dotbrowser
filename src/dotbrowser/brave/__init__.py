"""Brave-browser-specific subcommands."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotbrowser.brave import shortcuts as shortcuts_mod

DEFAULT_PROFILE_ROOT = Path.home() / ".config" / "BraveSoftware" / "Brave-Browser"


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("brave", help="Brave browser commands")
    p.add_argument(
        "--profile-root",
        type=Path,
        default=DEFAULT_PROFILE_ROOT,
        help=f"default: {DEFAULT_PROFILE_ROOT}",
    )
    p.add_argument(
        "--profile",
        default="Default",
        help="profile dir name (default: Default)",
    )
    sub = p.add_subparsers(dest="module", required=True, metavar="MODULE")
    shortcuts_mod.register(sub)
