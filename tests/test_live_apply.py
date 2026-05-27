from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dotbrowser._base import orchestrator as orch
from dotbrowser._base import live_apply
from dotbrowser._base.utils import Plan


def _args(profile_root: Path, config: Path, **overrides) -> argparse.Namespace:
    values = {
        "profile_root": profile_root,
        "profile": "Default",
        "config": str(config),
        "dry_run": False,
        "kill_browser": False,
        "live_port": None,
        "allow_http": False,
        "expect_sha256": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "Default"
    profile.mkdir()
    (profile / "Preferences").write_text(json.dumps({"foo": {"bar": 0}}))
    return tmp_path


def _build_plan(prefs_path: Path, _prefs: dict, _doc: dict) -> list[Plan]:
    def apply_fn(prefs: dict) -> None:
        prefs["foo"]["bar"] = 1

    return [
        Plan(
            namespace="settings",
            diff_lines=["  ~ foo.bar: 0 -> 1"],
            apply_fn=apply_fn,
            verify_fn=lambda _prefs: None,
            state_path=prefs_path.with_name("Preferences.dotbrowser.settings.json"),
            state_payload={"managed_keys": ["foo.bar"]},
        )
    ]


def test_running_browser_with_live_port_uses_live_apply_without_writing_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = _profile(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text("[settings]\nfoo.bar = 1\n")
    prefs_path = profile_root / "Default" / "Preferences"
    before = prefs_path.read_bytes()
    calls: list[tuple[int, Path, list[str]]] = []
    remembered: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(
        orch,
        "remember_devtools_port",
        lambda root, profile, port: remembered.append((root, profile, port)),
    )

    def live_apply_fn(port: int, got_prefs_path: Path, _prefs: dict, plans: list[Plan]) -> None:
        calls.append((port, got_prefs_path, [p.namespace for p in plans]))

    orch.cmd_apply(
        _args(profile_root, cfg, live_port=9333),
        display_name="Brave",
        running_fn=lambda: True,
        pids_fn=lambda: ["123"],
        find_cmdline_fn=lambda: ["brave"],
        kill_fn=lambda: (_ for _ in ()).throw(AssertionError("must not kill")),
        restart_fn=lambda _cmd: [],
        build_plans_fn=_build_plan,
        live_apply_fn=live_apply_fn,
    )

    assert calls == [(9333, prefs_path, ["settings"])]
    assert remembered == [(profile_root, "Default", 9333)]
    assert prefs_path.read_bytes() == before


def test_live_port_and_kill_browser_are_mutually_exclusive(tmp_path: Path) -> None:
    profile_root = _profile(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text("[settings]\nfoo.bar = 1\n")

    with pytest.raises(SystemExit, match="--live-port.*--kill-browser"):
        orch.cmd_apply(
            _args(profile_root, cfg, live_port=9333, kill_browser=True),
            display_name="Brave",
            running_fn=lambda: False,
            pids_fn=lambda: [],
            find_cmdline_fn=lambda: None,
            kill_fn=lambda: None,
            restart_fn=lambda _cmd: [],
            build_plans_fn=_build_plan,
            live_apply_fn=lambda *_args: None,
        )


def test_running_browser_without_live_port_gracefully_relaunches_for_live_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = _profile(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text("[settings]\nfoo.bar = 1\n")
    prefs_path = profile_root / "Default" / "Preferences"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(orch, "find_devtools_port", lambda _root, _profile: None)
    monkeypatch.setattr(orch, "pick_unused_port", lambda: 9444)
    monkeypatch.setattr(
        orch,
        "wait_for_devtools_endpoint",
        lambda port, display_name: calls.append(("wait", port)),
    )
    monkeypatch.setattr(
        orch,
        "remember_devtools_port",
        lambda root, profile, port: calls.append(
            ("remember", (root, profile, port))
        ),
    )

    def live_apply_fn(port: int, got_prefs_path: Path, _prefs: dict, plans: list[Plan]) -> None:
        calls.append(("live", (port, got_prefs_path, [p.namespace for p in plans])))

    orch.cmd_apply(
        _args(profile_root, cfg),
        display_name="Brave",
        running_fn=lambda: True,
        pids_fn=lambda: ["123"],
        find_cmdline_fn=lambda: ["brave"],
        kill_fn=lambda: (_ for _ in ()).throw(AssertionError("must not force-kill")),
        restart_fn=lambda _cmd: [],
        build_plans_fn=_build_plan,
    live_apply_fn=live_apply_fn,
        graceful_close_fn=lambda: calls.append(("close", None)),
        launch_live_fn=lambda root, profile, port, url: calls.append(
            ("launch", (root, profile, port, url))
        ) or ["brave"],
    )

    assert calls == [
        ("close", None),
        ("launch", (profile_root, "Default", 9444, None)),
        ("wait", 9444),
        ("live", (9444, prefs_path, ["settings"])),
        ("remember", (profile_root, "Default", 9444)),
    ]


def test_running_browser_reuses_existing_devtools_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = _profile(tmp_path)
    cfg = tmp_path / "config.toml"
    cfg.write_text("[settings]\nfoo.bar = 1\n")
    calls: list[str] = []

    monkeypatch.setattr(orch, "find_devtools_port", lambda _root, _profile: 9555)

    orch.cmd_apply(
        _args(profile_root, cfg),
        display_name="Brave",
        running_fn=lambda: True,
        pids_fn=lambda: ["123"],
        find_cmdline_fn=lambda: ["brave"],
        kill_fn=lambda: (_ for _ in ()).throw(AssertionError("must not force-kill")),
        restart_fn=lambda _cmd: [],
        build_plans_fn=_build_plan,
        live_apply_fn=lambda port, *_args: calls.append(f"live:{port}"),
        graceful_close_fn=lambda: calls.append("close"),
        launch_live_fn=lambda *_args: calls.append("launch") or ["brave"],
    )

    assert calls == ["live:9555"]


def test_changed_leaf_paths_expands_new_nested_dicts() -> None:
    before = {"brave": {}}
    after = {"brave": {"tabs": {"vertical_tabs_enabled": True}}}

    assert live_apply.changed_leaf_paths(before, after) == [
        (("brave", "tabs", "vertical_tabs_enabled"), True)
    ]
