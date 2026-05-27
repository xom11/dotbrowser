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
from dotbrowser.chrome import register as register_chrome
from dotbrowser.edge import register as register_edge
from dotbrowser.vivaldi import register as register_vivaldi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotbrowser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
Manage Chromium-based browser customizations as TOML dotfiles.

Command shape:
  dotbrowser <browser> [browser-options] <action> [action-options]

Capability overview:
  Brave          [shortcuts] [settings] [pwa]  live apply; stable/beta/nightly
  Vivaldi        [shortcuts] [settings] [pwa]  live apply; settings schema search
  Microsoft Edge [settings] [pwa]              offline apply only
  Google Chrome  [settings] [pwa]              offline apply only

`[settings]` writes unprotected Preferences keys. `[pwa]` manages
force-installed web apps through browser policy. Brave and Vivaldi also
manage keyboard shortcuts.""",
        epilog="""\
Typical workflow:
  dotbrowser brave init -o brave.toml
  dotbrowser brave apply --dry-run brave.toml
  dotbrowser brave apply brave.toml
  dotbrowser brave export -o snapshot.toml
  dotbrowser brave restore --list

Use `dotbrowser <browser> --help` to see browser capabilities and
`dotbrowser <browser> <action> --help` for safety details and examples.""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="browser", required=True, metavar="BROWSER")
    register_brave(sub)
    register_chrome(sub)
    register_edge(sub)
    register_vivaldi(sub)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    normalize = getattr(args, "_normalize_args", None)
    if normalize is not None:
        normalize(args)
    args.func(args)
