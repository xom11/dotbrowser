"""Tests for platform-aware logic (profile root, process name).

We avoid actually invoking subprocess on a real Brave; we just verify
that the module-level dispatch picks the right code path per
`sys.platform`.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "platform,expected_suffix",
    [
        ("darwin", Path("Library") / "Application Support" / "BraveSoftware" / "Brave-Browser"),
        ("linux", Path(".config") / "BraveSoftware" / "Brave-Browser"),
        ("linux2", Path(".config") / "BraveSoftware" / "Brave-Browser"),
    ],
)
def test_default_profile_root_supported(monkeypatch, platform, expected_suffix) -> None:
    monkeypatch.setattr("sys.platform", platform)
    import dotbrowser.brave as brave_pkg
    importlib.reload(brave_pkg)
    root = brave_pkg._default_profile_root()
    assert root is not None
    assert root.is_absolute()
    # The trailing path components should match what we expect for this OS
    assert str(root).endswith(str(expected_suffix))


def test_default_profile_root_unsupported_returns_none(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    import dotbrowser.brave as brave_pkg
    importlib.reload(brave_pkg)
    assert brave_pkg._default_profile_root() is None


@pytest.mark.parametrize(
    "platform,expected_name",
    [("darwin", "Brave Browser"), ("linux", "brave"), ("linux2", "brave")],
)
def test_proc_name_per_platform(monkeypatch, platform, expected_name) -> None:
    monkeypatch.setattr("sys.platform", platform)
    from dotbrowser.brave import shortcuts
    importlib.reload(shortcuts)
    assert shortcuts._brave_proc_name() == expected_name


def test_restart_uses_open_on_macos(monkeypatch) -> None:
    """On macOS, restart_brave should shell out to `open -a "Brave Browser"`
    rather than launching the captured argv[0] directly. We capture the
    Popen call instead of actually spawning anything."""
    monkeypatch.setattr("sys.platform", "darwin")
    from dotbrowser.brave import shortcuts
    importlib.reload(shortcuts)

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    monkeypatch.setattr(shortcuts.subprocess, "Popen", FakePopen)
    used = shortcuts.restart_brave([
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "--enable-features=Foo",
    ])
    assert used[0] == "open"
    assert used[1] == "-a"
    assert used[2] == "Brave Browser"
    assert "--args" in used
    assert "--enable-features=Foo" in used
    assert captured["cmd"] == used
    # Should detach from the current shell so dotbrowser can exit
    assert captured["kwargs"].get("start_new_session") is True


def test_read_cmdline_macos_does_not_shlex_split(monkeypatch) -> None:
    """Regression: the macOS executable path contains literal spaces
    (`/Applications/Brave Browser.app/Contents/MacOS/Brave Browser`). Earlier
    code shlex-split the `ps -o command=` output, which mangled the path
    into 4 garbage tokens and broke `restart_brave`. Now we keep it as a
    single element."""
    monkeypatch.setattr("sys.platform", "darwin")
    from dotbrowser.brave import shortcuts
    importlib.reload(shortcuts)

    raw = b"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser\n"

    def fake_check_output(cmd, **kwargs):
        assert cmd[0] == "ps"
        return raw

    monkeypatch.setattr(shortcuts.subprocess, "check_output", fake_check_output)
    out = shortcuts._read_cmdline("12345")
    assert out == ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]


def test_restart_uses_wrapper_on_linux(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    from dotbrowser.brave import shortcuts
    importlib.reload(shortcuts)

    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: "/usr/bin/brave-browser" if name == "brave-browser" else None)

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd

    monkeypatch.setattr(shortcuts.subprocess, "Popen", FakePopen)
    used = shortcuts.restart_brave(["/opt/brave.com/brave/brave", "--flag=1"])
    # Inner binary swapped for wrapper
    assert used[0] == "/usr/bin/brave-browser"
    assert "--flag=1" in used
