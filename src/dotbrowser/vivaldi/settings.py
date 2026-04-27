"""Vivaldi settings -- thin wrapper around shared settings logic."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotbrowser._base import settings as _base
from dotbrowser._base.utils import Plan

NAMESPACE = _base.NAMESPACE

# Re-export internals that tests may use
_MISSING = _base._MISSING
_split_key = _base._split_key
_get_value = _base._get_value
_set_value = _base._set_value
_pop_value = _base._pop_value
_is_mac_protected = _base._is_mac_protected
_validate_table = _base._validate_table
_get_managed_keys = _base._get_managed_keys
_format_toml_value = _base._format_toml_value
diff_summary = _base.diff_summary


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    return _base.plan_apply("vivaldi", prefs_path, prefs, raw_table)


def cmd_dump(args: argparse.Namespace) -> None:
    _base.cmd_dump("vivaldi", args)


def cmd_blocked(args: argparse.Namespace) -> None:
    _base.cmd_blocked("vivaldi", args)


def register(subparsers: argparse._SubParsersAction) -> None:
    _base.register("vivaldi", subparsers)
