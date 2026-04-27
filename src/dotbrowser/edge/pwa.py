"""Edge PWA -- browser-specific config + thin wrappers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotbrowser._base import pwa as _base
from dotbrowser._base.utils import Plan, find_preferences

NAMESPACE = _base.NAMESPACE
POLICY_KEY = _base.POLICY_KEY

_PWA_CONFIG = _base.PwaConfig(
    browser_name="edge",
    linux_policy_path="/etc/opt/edge/policies/managed/dotbrowser-pwa.json",
    macos_plist_path="/Library/Managed Preferences/com.microsoft.edgemac.plist",
    windows_registry_key=r"Software\Policies\Microsoft\Edge",
)

POLICY_FILE = _base.default_policy_file(_PWA_CONFIG)
_WINDOWS_POLICY_KEY = _PWA_CONFIG.windows_registry_key


def _read_existing_payload() -> dict:
    return _base.read_existing_payload(POLICY_FILE, _WINDOWS_POLICY_KEY)


def _read_current_policy() -> dict[str, dict]:
    data = _read_existing_payload()
    entries = data.get(POLICY_KEY, [])
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("url"), str):
            out[e["url"]] = e
    return out


def _build_policy_payload(entries: list[dict]) -> bytes:
    return _base.build_policy_payload(POLICY_FILE, _WINDOWS_POLICY_KEY, entries)


def _sudo_write_policy(entries: list[dict]) -> None:
    _base.sudo_write_policy(POLICY_FILE, _WINDOWS_POLICY_KEY, entries)


def _validate_table(raw: object) -> list[str]:
    return _base.validate_table(raw)


def diff_summary(current: dict[str, dict], target_urls: list[str]) -> list[str]:
    return _base.diff_summary(current, target_urls)


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    return _base.plan_apply(
        _PWA_CONFIG, POLICY_FILE, _sudo_write_policy,
        _read_current_policy, prefs_path, prefs, raw_table,
    )


def cmd_dump(args: argparse.Namespace) -> None:
    _base.cmd_dump(
        "edge", POLICY_FILE, _WINDOWS_POLICY_KEY,
        _read_current_policy, args,
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    _base.register(
        "edge", POLICY_FILE, _WINDOWS_POLICY_KEY,
        _read_current_policy, cmd_dump, subparsers,
    )
