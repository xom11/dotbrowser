"""Live settings apply through a Chromium browser's Settings WebUI."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from dotbrowser._base import live_apply as _live
from dotbrowser._base.cdp import CdpClient
from dotbrowser._base.utils import Plan


def _page_target(client: CdpClient, display_name: str) -> dict:
    for target in client.list_targets():
        if target.get("type") == "page":
            return target
    sys.exit(f"error: live apply found no page target to drive {display_name}")


def _setting_changes(
    display_name: str, before: dict, target: dict
) -> list[tuple[str, Any]]:
    changes = _live.changed_leaf_paths(before, target)
    _live.refuse_live_removals(display_name, changes)
    return [(".".join(parts), value) for parts, value in changes]


def _preflight_script(changes: list[tuple[str, Any]]) -> str | None:
    if not changes:
        return None
    keys_json = json.dumps([key for key, _value in changes], separators=(",", ":"))
    return (
        "(async () => {"
        f"const keys = {keys_json};"
        "const exists = key => new Promise(resolve => {"
        "chrome.settingsPrivate.getPref(key, pref => {"
        "const err = chrome.runtime.lastError;"
        "resolve(!err && !!pref);"
        "});"
        "});"
        "const unsupported = [];"
        "for (const key of keys) {"
        "if (!(await exists(key))) unsupported.push(key);"
        "}"
        "return unsupported;"
        "})()"
    )


def _settings_script(changes: list[tuple[str, Any]]) -> str | None:
    if not changes:
        return None
    calls = "".join(
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
        "await new Promise(r => setTimeout(r, 300));"
        "return true;"
        "})()"
    )


def apply_live(
    display_name: str,
    settings_url: str,
    port: int,
    prefs_path: Path,
    prefs: dict,
    plans: list[Plan],
) -> None:
    target_prefs = _live.compute_target_prefs(prefs, plans)
    changes = _setting_changes(display_name, prefs, target_prefs)
    client: CdpClient | None = None
    target: dict | None = None
    preflight = _preflight_script(changes)
    if preflight is not None:
        client = CdpClient(port)
        target = _page_target(client, display_name)
        client.navigate(target, settings_url)
        time.sleep(0.5)
        result = client.evaluate(target, preflight)
        unsupported = (
            [key for key in result if isinstance(key, str)]
            if isinstance(result, list)
            else []
        )
        if unsupported:
            raise _live.LiveApplyUnsupported(display_name, unsupported)

    has_pref_changes = any(
        not plan.empty and plan.namespace == "settings" for plan in plans
    )
    if has_pref_changes:
        _live.backup_preferences(prefs_path)

    _live.apply_external_plans(plans)

    settings_script = _settings_script(changes)
    if settings_script is not None and client is not None and target is not None:
        client.evaluate(target, settings_script)

    _live.write_state_files(plans)
    print(f"ok -- live applied through {display_name} DevTools endpoint")
