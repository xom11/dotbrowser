from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotbrowser._base import live_apply as shared_live
from dotbrowser._base.utils import Plan
from dotbrowser.chrome import live as chrome_live
from dotbrowser.edge import live as edge_live


class FakeCdpClient:
    def __init__(self, port: int, evaluation_results: list[object] | None = None):
        self.port = port
        self.targets = [{"type": "page", "url": "about:blank"}]
        self.navigations: list[str] = []
        self.evaluations: list[str] = []
        self.evaluation_results = iter(evaluation_results or [])

    def list_targets(self) -> list[dict]:
        return self.targets

    def navigate(self, target: dict, url: str) -> None:
        self.navigations.append(url)
        target["url"] = url

    def evaluate(self, target: dict, expression: str):
        self.evaluations.append(expression)
        return next(self.evaluation_results, [])


def _settings_plan() -> Plan:
    def apply_fn(target: dict) -> None:
        target["bookmark_bar"]["show_on_all_tabs"] = True

    return Plan(
        namespace="settings",
        diff_lines=["changed"],
        apply_fn=apply_fn,
        verify_fn=lambda _prefs: None,
    )


@pytest.mark.parametrize(
    ("module", "settings_url"),
    [
        (chrome_live, "chrome://settings/appearance"),
        (edge_live, "edge://settings/appearance"),
    ],
)
def test_chromium_live_settings_use_settings_private(
    module, settings_url: str, tmp_path: Path, monkeypatch
) -> None:
    prefs_path = tmp_path / "Default" / "Preferences"
    prefs_path.parent.mkdir()
    prefs = {"bookmark_bar": {"show_on_all_tabs": False}}
    prefs_path.write_text(json.dumps(prefs))
    fake = FakeCdpClient(9333)
    monkeypatch.setattr(module._shared, "CdpClient", lambda port: fake)

    module.apply_live(9333, prefs_path, prefs, [_settings_plan()])

    assert settings_url in fake.navigations
    assert any("chrome.settingsPrivate.getPref" in expr for expr in fake.evaluations)
    assert any("chrome.settingsPrivate.setPref" in expr for expr in fake.evaluations)


def test_chrome_live_unavailable_pref_raises_before_backup_or_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    prefs_path = tmp_path / "Default" / "Preferences"
    prefs_path.parent.mkdir()
    prefs = {"ntp": {"shortcust_visible": True}}
    prefs_path.write_text(json.dumps(prefs))

    def apply_fn(target: dict) -> None:
        target["ntp"]["shortcust_visible"] = False

    plan = Plan(
        namespace="settings",
        diff_lines=["changed"],
        apply_fn=apply_fn,
        verify_fn=lambda _prefs: None,
    )
    fake = FakeCdpClient(9333, evaluation_results=[["ntp.shortcust_visible"]])
    monkeypatch.setattr(chrome_live._shared, "CdpClient", lambda port: fake)

    with pytest.raises(shared_live.LiveApplyUnsupported):
        chrome_live.apply_live(9333, prefs_path, prefs, [plan])

    assert any("chrome.settingsPrivate.getPref" in expr for expr in fake.evaluations)
    assert not any("chrome.settingsPrivate.setPref" in expr for expr in fake.evaluations)
    assert list(prefs_path.parent.glob("Preferences.bak.*")) == []
