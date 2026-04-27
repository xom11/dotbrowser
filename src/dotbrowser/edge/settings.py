"""Edge settings -- thin wrapper around shared settings logic."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotbrowser._base import settings as _base
from dotbrowser._base.utils import Plan

NAMESPACE = _base.NAMESPACE

plan_apply = lambda prefs_path, prefs, raw_table: _base.plan_apply(
    "edge", prefs_path, prefs, raw_table
)
diff_summary = _base.diff_summary


def cmd_dump(args: argparse.Namespace) -> None:
    _base.cmd_dump("edge", args)


def register(subparsers: argparse._SubParsersAction) -> None:
    _base.register("edge", subparsers)
