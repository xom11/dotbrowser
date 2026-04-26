"""Top-level CLI for dotbrowser.

Usage:
    dotbrowser brave   apply <config>        # writes [shortcuts] + [settings] + [pwa]
    dotbrowser vivaldi apply <config>        # same, for Vivaldi
    dotbrowser <browser> shortcuts dump|list
    dotbrowser <browser> settings  dump [keys...]
    dotbrowser <browser> pwa       dump

New browser support is added by writing a `dotbrowser/<browser>/__init__.py`
that exposes `register(subparsers)` to mount its subcommands.
"""
from __future__ import annotations

import argparse

from dotbrowser import __version__
from dotbrowser.brave import register as register_brave
from dotbrowser.vivaldi import register as register_vivaldi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotbrowser",
        description="Manage browser settings as dotfiles.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="browser", required=True, metavar="BROWSER")
    register_brave(sub)
    register_vivaldi(sub)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
