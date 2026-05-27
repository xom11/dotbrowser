"""Live apply support for a running Vivaldi instance."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from dotbrowser._base.cdp import CdpClient
from dotbrowser._base import live_apply as _live
from dotbrowser._base.utils import Plan
from dotbrowser.vivaldi import schema as _schema
from dotbrowser.vivaldi import shortcuts as shortcuts_mod


def _is_actions_path(parts: tuple[str, ...]) -> bool:
    return parts[:2] == shortcuts_mod.ACTIONS_KEY_PATH


def _vivaldi_target(client: CdpClient) -> dict:
    targets = client.list_targets()
    for target in targets:
        url = str(target.get("url", ""))
        if url.startswith("chrome-extension://") and url.endswith("/main.html"):
            return target
    for target in targets:
        url = str(target.get("url", ""))
        if not url.startswith("chrome-extension://"):
            continue
        try:
            if client.evaluate(
                target,
                "typeof vivaldi === 'object' && "
                "typeof vivaldi.prefs === 'object'",
            ):
                return target
        except SystemExit:
            continue
    sys.exit("error: live apply found no Vivaldi UI target to drive settings")


def _value_for_api(path: str, value: Any) -> Any:
    defn = _schema.lookup(_schema.load_schema(), path)
    if (
        isinstance(defn, dict)
        and defn.get("type") == "enum"
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        enum_values = defn.get("enum_values")
        if isinstance(enum_values, dict):
            for name, raw in enum_values.items():
                if raw == value:
                    return name
    return value


def _setting_changes(before: dict, target: dict) -> list[tuple[str, Any]]:
    changes = [
        (parts, value)
        for parts, value in _live.changed_leaf_paths(before, target)
        if not _is_actions_path(parts)
    ]
    _live.refuse_live_removals("Vivaldi", changes)
    return [
        (".".join(parts), _value_for_api(".".join(parts), value))
        for parts, value in changes
    ]


def _settings_script(changes: list[tuple[str, Any]]) -> str | None:
    if not changes:
        return None
    calls = "".join(
        "vivaldi.prefs.set({"
        f"path:{json.dumps(path)},value:{json.dumps(value)}"
        "});"
        for path, value in changes
    )
    return (
        "(async () => {"
        f"{calls}"
        "await new Promise(r => setTimeout(r, 300));"
        "return true;"
        "})()"
    )


def _actions_script(actions: Any) -> str:
    return (
        "(async () => {"
        "vivaldi.prefs.set({"
        f"path:'vivaldi.actions',value:{json.dumps(actions)}"
        "});"
        "await new Promise(r => setTimeout(r, 300));"
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
    target = _vivaldi_target(client)

    settings_script = _settings_script(_setting_changes(prefs, target_prefs))
    if settings_script is not None:
        client.evaluate(target, settings_script)

    before_actions = _live.get_path(prefs, shortcuts_mod.ACTIONS_KEY_PATH)
    target_actions = _live.get_path(target_prefs, shortcuts_mod.ACTIONS_KEY_PATH)
    if target_actions is not _live.MISSING and before_actions != target_actions:
        client.evaluate(target, _actions_script(target_actions))
        client.reload(target)

    _live.write_state_files(plans)
    print("ok -- live applied through Vivaldi DevTools endpoint")
