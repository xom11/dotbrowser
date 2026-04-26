"""Shared pytest fixtures for dotbrowser tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def fake_profile_root(tmp_path: Path) -> Path:
    """A directory that mimics Brave's profile-root layout: a `Default`
    sub-folder containing a minimal `Preferences` JSON.

    The synthesized `brave.default_accelerators` includes a few real
    Chromium command IDs so that the reset-to-default code path has
    something to reset to.
    """
    from dotbrowser.brave.command_ids import NAME_TO_ID

    profile = tmp_path / "Default"
    profile.mkdir()
    prefs = {
        "brave": {
            # Pre-existing user customization (so `dump` has something to emit).
            # focus_location is already overridden; new_tab is at default.
            "accelerators": {
                str(NAME_TO_ID["focus_location"]): ["Alt+KeyL"],
                str(NAME_TO_ID["new_tab"]): ["Control+KeyT"],
            },
            "default_accelerators": {
                str(NAME_TO_ID["focus_location"]): ["Control+KeyL", "Alt+KeyD"],
                str(NAME_TO_ID["new_tab"]): ["Control+KeyT"],
                str(NAME_TO_ID["toggle_sidebar"]): ["Control+Shift+KeyS"],
            },
        },
        "some": {"unrelated": "preference"},
    }
    (profile / "Preferences").write_text(json.dumps(prefs))
    return tmp_path
