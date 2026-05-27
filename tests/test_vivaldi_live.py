from __future__ import annotations

import json
from pathlib import Path

from dotbrowser._base.utils import Plan
from dotbrowser.vivaldi import live


class FakeCdpClient:
    def __init__(self, port: int):
        self.port = port
        self.targets = [
            {
                "type": "app",
                "url": "chrome-extension://mpognobbkildjkofajifpdfhcoklimli/main.html",
            }
        ]
        self.evaluations: list[str] = []
        self.reloads = 0

    def list_targets(self) -> list[dict]:
        return self.targets

    def evaluate(self, target: dict, expression: str):
        self.evaluations.append(expression)
        return None

    def reload(self, target: dict) -> None:
        self.reloads += 1


def test_vivaldi_live_apply_uses_vivaldi_prefs_and_reloads_ui(
    tmp_path: Path, monkeypatch
) -> None:
    prefs_path = tmp_path / "Default" / "Preferences"
    prefs_path.parent.mkdir()
    prefs = {
        "vivaldi": {
            "tabs": {"bar": {"position": 0}},
            "panels": {"position": 0},
            "auto_hide": {"enabled": False},
            "actions": [
                {
                    "COMMAND_NEW_TAB": {
                        "shortcuts": ["ctrl+t"],
                        "gestures": ["2"],
                    }
                }
            ],
        }
    }
    prefs_path.write_text(json.dumps(prefs))

    monkeypatch.setattr(
        live._schema,
        "load_schema",
        lambda: {
            "vivaldi.tabs.bar.position": {
                "type": "enum",
                "enum_values": {"top": 0, "left": 1},
            },
            "vivaldi.panels.position": {
                "type": "enum",
                "enum_values": {"left": 0, "right": 1},
            },
            "vivaldi.auto_hide.enabled": {"type": "boolean"},
        },
    )

    def apply_fn(target: dict) -> None:
        target["vivaldi"]["tabs"]["bar"]["position"] = 1
        target["vivaldi"]["panels"]["position"] = 1
        target["vivaldi"]["auto_hide"]["enabled"] = True
        target["vivaldi"]["actions"][0]["COMMAND_NEW_TAB"]["shortcuts"] = ["ctrl+shift+y"]

    plan = Plan(
        namespace="shortcuts",
        diff_lines=["changed"],
        apply_fn=apply_fn,
        verify_fn=lambda _prefs: None,
        state_path=prefs_path.with_name("Preferences.dotbrowser.shortcuts.json"),
        state_payload={"managed_ids": ["COMMAND_NEW_TAB"]},
    )

    fake = FakeCdpClient(9334)
    monkeypatch.setattr(live, "CdpClient", lambda port: fake)

    live.apply_live(9334, prefs_path, prefs, [plan])

    assert any(
        "vivaldi.prefs.set" in expr
        and "vivaldi.tabs.bar.position" in expr
        and '"left"' in expr
        for expr in fake.evaluations
    )
    assert any(
        "vivaldi.prefs.set" in expr
        and "vivaldi.panels.position" in expr
        and '"right"' in expr
        and "vivaldi.auto_hide.enabled" in expr
        and "true" in expr
        for expr in fake.evaluations
    )
    assert any(
        "vivaldi.prefs.set" in expr
        and "vivaldi.actions" in expr
        and "ctrl+shift+y" in expr
        for expr in fake.evaluations
    )
    assert fake.reloads == 1
    state = json.loads(prefs_path.with_name("Preferences.dotbrowser.shortcuts.json").read_text())
    assert state["managed_ids"] == ["COMMAND_NEW_TAB"]
