"""Pure-logic tests — no Brave process, no real profile, no subprocess."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dotbrowser.brave import shortcuts as sc


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_valid(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'focus_location = ["Control+KeyL", "Alt+KeyD"]\n'
        'new_tab = ["Control+KeyT"]\n'
    )
    assert sc.load_config(cfg) == {
        "focus_location": ["Control+KeyL", "Alt+KeyD"],
        "new_tab": ["Control+KeyT"],
    }


def test_load_config_missing_table_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("# nothing here\n")
    assert sc.load_config(cfg) == {}


def test_load_config_rejects_non_string_values(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('[shortcuts]\nfocus_location = "Control+KeyL"\n')
    with pytest.raises(SystemExit, match="must be a list of strings"):
        sc.load_config(cfg)


def test_load_config_rejects_non_table_root(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("shortcuts = []\n")
    with pytest.raises(SystemExit, match=r"\[shortcuts\] must be a table"):
        sc.load_config(cfg)


# ---------------------------------------------------------------------------
# resolve_command_ids
# ---------------------------------------------------------------------------

def test_resolve_known_names() -> None:
    from dotbrowser.brave.command_ids import NAME_TO_ID

    out = sc.resolve_command_ids({"focus_location": ["Control+KeyL"]})
    # CLI stores command IDs stringified (matches Brave's Preferences format)
    assert out == {str(NAME_TO_ID["focus_location"]): ["Control+KeyL"]}


def test_resolve_unknown_name_exits() -> None:
    with pytest.raises(SystemExit, match="unknown command name"):
        sc.resolve_command_ids({"definitely_not_a_real_command": ["F13"]})


# ---------------------------------------------------------------------------
# diff_summary
# ---------------------------------------------------------------------------

def test_diff_summary_added_modified_removed() -> None:
    from dotbrowser.brave.command_ids import NAME_TO_ID

    focus = str(NAME_TO_ID["focus_location"])
    new_tab = str(NAME_TO_ID["new_tab"])
    unknown_id = "999999"  # outside the mapping → renders as <unknown:...>

    current = {focus: ["Control+KeyL"], new_tab: ["Control+KeyT"]}
    target = {
        focus: ["Alt+KeyD"],            # modified
        unknown_id: ["F12"],             # added (unknown id falls through)
    }
    removed = {new_tab}                  # removed (was managed, now gone)
    lines = sc.diff_summary(current, target, removed)
    joined = "\n".join(lines)
    assert "~ focus_location" in joined
    assert "+ <unknown:" + unknown_id + ">" in joined
    assert "- new_tab" in joined and "reset to default" in joined


def test_diff_summary_no_changes_returns_empty() -> None:
    current = {"35012": ["Control+KeyL"]}
    target = {"35012": ["Control+KeyL"]}
    assert sc.diff_summary(current, target, set()) == []


# ---------------------------------------------------------------------------
# write_atomic
# ---------------------------------------------------------------------------

def test_write_atomic_replaces_file(tmp_path: Path) -> None:
    p = tmp_path / "Preferences"
    p.write_text(json.dumps({"old": True}))
    sc.write_atomic(p, {"new": True})
    assert json.loads(p.read_text()) == {"new": True}
    # No leftover .tmp file
    assert not p.with_suffix(p.suffix + ".tmp").exists()


# ---------------------------------------------------------------------------
# state file (managed_ids sidecar)
# ---------------------------------------------------------------------------

def test_managed_ids_round_trip(tmp_path: Path) -> None:
    prefs = tmp_path / "Preferences"
    prefs.write_text("{}")
    assert sc._get_managed_ids(prefs) == set()

    sc._set_managed_ids(prefs, {"35012", "34014"})
    assert sc._get_managed_ids(prefs) == {"35012", "34014"}

    # Sidecar file is named <Preferences>.dotbrowser.shortcuts.json
    sidecar = prefs.with_name(prefs.name + ".dotbrowser.shortcuts.json")
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    # IDs are sorted numerically, not lexicographically
    assert data["managed_ids"] == ["34014", "35012"]


def test_managed_ids_handles_corrupt_sidecar(tmp_path: Path) -> None:
    prefs = tmp_path / "Preferences"
    sidecar = prefs.with_name(prefs.name + ".dotbrowser.shortcuts.json")
    sidecar.write_text("not json {{")
    assert sc._get_managed_ids(prefs) == set()


# ---------------------------------------------------------------------------
# find_preferences
# ---------------------------------------------------------------------------

def test_find_preferences_ok(fake_profile_root: Path) -> None:
    p = sc.find_preferences(fake_profile_root, "Default")
    assert p == fake_profile_root / "Default" / "Preferences"


def test_find_preferences_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Preferences not found"):
        sc.find_preferences(tmp_path, "NoSuchProfile")


# ---------------------------------------------------------------------------
# get_nested
# ---------------------------------------------------------------------------

def test_get_nested_creates_intermediate_dicts() -> None:
    d: dict = {}
    inner = sc.get_nested(d, ("a", "b", "c"))
    inner["x"] = 1
    assert d == {"a": {"b": {"c": {"x": 1}}}}
