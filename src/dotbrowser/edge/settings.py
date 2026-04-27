"""Edge settings -- thin wrapper around shared settings logic."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotbrowser._base import settings as _base
from dotbrowser._base.utils import Plan

NAMESPACE = _base.NAMESPACE

diff_summary = _base.diff_summary


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    return _base.plan_apply("edge", prefs_path, prefs, raw_table)


def cmd_dump(args: argparse.Namespace) -> None:
    _base.cmd_dump("edge", args)


def cmd_blocked(args: argparse.Namespace) -> None:
    _base.cmd_blocked("edge", args)


def register(subparsers: argparse._SubParsersAction) -> None:
    _base.register("edge", subparsers)
