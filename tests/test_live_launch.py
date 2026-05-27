from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from dotbrowser._base import orchestrator as orch


def test_cmd_launch_refuses_to_add_devtools_to_already_running_browser(
    tmp_path: Path,
) -> None:
    calls: list[tuple[Path, str, int, str | None]] = []

    with pytest.raises(SystemExit, match="already running"):
        orch.cmd_launch(
            argparse.Namespace(
                profile_root=tmp_path,
                profile="Default",
                live_port=9333,
                url=None,
                dry_run=False,
            ),
            display_name="Brave",
            running_fn=lambda: True,
            launch_fn=lambda *args: calls.append(args),
        )

    assert calls == []


def test_cmd_launch_passes_profile_and_port_to_browser_launcher(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str, int, str | None]] = []
    remembered: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(
        orch,
        "remember_devtools_port",
        lambda root, profile, port: remembered.append((root, profile, port)),
    )

    orch.cmd_launch(
        argparse.Namespace(
            profile_root=tmp_path,
            profile="Profile 1",
            live_port=9333,
            url="https://example.com/",
            dry_run=False,
        ),
        display_name="Brave",
        running_fn=lambda: False,
        launch_fn=lambda *args: calls.append(args) or ["brave", "--remote-debugging-port=9333"],
    )

    assert calls == [(tmp_path, "Profile 1", 9333, "https://example.com/")]
    assert remembered == [(tmp_path, "Profile 1", 9333)]
    assert "127.0.0.1:9333" in capsys.readouterr().out
