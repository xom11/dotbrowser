"""Live apply support for a running Brave instance."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from dotbrowser._base.cdp import CdpClient
from dotbrowser._base import live_apply as _live
from dotbrowser._base.utils import Plan
from dotbrowser.brave import shortcuts as shortcuts_mod


_SETTINGS_URL = "chrome://settings/appearance"
_SHORTCUTS_URL = "chrome://settings/system/shortcuts"


def _page_target(client: CdpClient) -> dict:
    for target in client.list_targets():
        if target.get("type") == "page":
            return target
    sys.exit("error: --live-port found no page target to drive Brave live apply")


def _is_shortcut_path(parts: tuple[str, ...]) -> bool:
    return parts[:2] in {
        shortcuts_mod.ACCELERATORS_KEY_PATH,
        shortcuts_mod.DEFAULT_ACCELERATORS_KEY_PATH,
    }


def _setting_changes(before: dict, target: dict) -> list[tuple[str, Any]]:
    changes = [
        (parts, value)
        for parts, value in _live.changed_leaf_paths(before, target)
        if not _is_shortcut_path(parts)
    ]
    _live.refuse_live_removals("Brave", changes)
    return [(".".join(parts), value) for parts, value in changes]


def _dict_at(prefs: dict, parts: tuple[str, ...]) -> dict[str, list[str]]:
    value = _live.get_path(prefs, parts)
    return value if isinstance(value, dict) else {}


def _shortcut_script(before: dict, target: dict) -> str | None:
    current = _dict_at(before, shortcuts_mod.ACCELERATORS_KEY_PATH)
    defaults = _dict_at(before, shortcuts_mod.DEFAULT_ACCELERATORS_KEY_PATH)
    desired = _dict_at(target, shortcuts_mod.ACCELERATORS_KEY_PATH)
    desired_changes: dict[str, list[str]] = {}
    all_ids = set(current) | set(desired)
    for cid in sorted(all_ids, key=lambda v: int(v)):
        old_keys = list(current[cid] if cid in current else defaults.get(cid, []))
        new_keys = list(desired.get(cid, []))
        if old_keys == new_keys:
            continue
        desired_changes[cid] = new_keys
    if not desired_changes:
        return None
    desired_json = json.dumps(desired_changes, separators=(",", ":"))
    return (
        "(async () => {"
        "const m = await import('/commands.bundle.js');"
        f"const desiredByCommand = {desired_json};"
        "const commandCache = m.commandsCache.cache || {};"
        "for (const [cidText, desired] of Object.entries(desiredByCommand)) {"
        "const cid = Number(cidText);"
        "const command = commandCache[cidText] || commandCache[cid];"
        "const current = (command?.accelerators || [])"
        ".map(a => a.codes || a.keys).filter(Boolean);"
        "for (const key of current) {"
        "if (!desired.includes(key)) "
        "m.commandsCache.unassignAccelerator(cid, key);"
        "}"
        "for (const key of desired) {"
        "if (!current.includes(key)) "
        "m.commandsCache.assignAccelerator(cid, {codes:key, keys:key});"
        "}"
        "}"
        "await new Promise(r => setTimeout(r, 300));"
        "return true;"
        "})()"
    )


def _settings_script(changes: list[tuple[str, Any]]) -> str | None:
    if not changes:
        return None
    calls = "\n".join(
        f"await setPref({json.dumps(key)}, {json.dumps(value)});"
        for key, value in changes
    )
    return (
        "(async () => {"
        "const setPref = (key, value) => new Promise((resolve, reject) => {"
        "chrome.settingsPrivate.setPref(key, value, '', ok => {"
        "const err = chrome.runtime.lastError;"
        "if (err) reject(new Error(err.message));"
        "else if (ok === false) reject(new Error('setPref returned false for ' + key));"
        "else resolve(ok);"
        "});"
        "});"
        f"{calls}"
        "return true;"
        "})()"
    )


def apply_live(port: int, prefs_path: Path, prefs: dict, plans: list[Plan]) -> None:
    target_prefs = _live.compute_target_prefs(prefs, plans)
    has_pref_changes = any(
        not plan.empty and plan.namespace in {"settings", "shortcuts"}
        for plan in plans
    )
    if has_pref_changes:
        _live.backup_preferences(prefs_path)

    _live.apply_external_plans(plans)

    client = CdpClient(port)
    target = _page_target(client)
    settings_script = _settings_script(_setting_changes(prefs, target_prefs))
    if settings_script is not None:
        client.navigate(target, _SETTINGS_URL)
        time.sleep(0.5)
        client.evaluate(target, settings_script)

    shortcut_script = _shortcut_script(prefs, target_prefs)
    if shortcut_script is not None:
        client.navigate(target, _SHORTCUTS_URL)
        time.sleep(0.5)
        client.evaluate(target, shortcut_script)

    _live.write_state_files(plans)
    print("ok -- live applied through Brave DevTools endpoint")
