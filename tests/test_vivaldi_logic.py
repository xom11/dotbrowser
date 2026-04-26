"""Pure-logic unit tests for the Vivaldi modules.

Companion to test_logic.py (Brave). Focuses on the parts that diverge
from Brave: the `vivaldi.actions[0]` list-of-one-dict shape, the
COMMAND_* validation, and the original-snapshot scheme that replaces
Brave's `default_accelerators` mirror.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotbrowser.vivaldi import shortcuts as sc
from dotbrowser.vivaldi import pwa


# ---------------------------------------------------------------------------
# shortcuts validation
# ---------------------------------------------------------------------------


def test_validate_accepts_command_names() -> None:
    out = sc._validate_table({"COMMAND_CLOSE_TAB": ["meta+w"]})
    assert out == {"COMMAND_CLOSE_TAB": ["meta+w"]}


def test_validate_rejects_non_command_name() -> None:
    """Anything not starting with COMMAND_ is almost always a typo —
    refuse rather than silently writing a dead entry."""
    with pytest.raises(SystemExit, match="COMMAND_"):
        sc._validate_table({"close_tab": ["meta+w"]})


def test_validate_rejects_non_string_list() -> None:
    with pytest.raises(SystemExit, match="must be a list of strings"):
        sc._validate_table({"COMMAND_FOO": "meta+w"})
    with pytest.raises(SystemExit, match="must be a list of strings"):
        sc._validate_table({"COMMAND_FOO": [1, 2]})


# ---------------------------------------------------------------------------
# _get_actions_dict — handles missing / malformed shapes gracefully
# ---------------------------------------------------------------------------


def test_get_actions_dict_creates_scaffold_when_missing() -> None:
    """A profile without `vivaldi.actions` is exotic but possible (e.g.
    a freshly-installed Vivaldi that hasn't booted yet). The helper
    must initialize the list-of-one-dict shape rather than crash."""
    prefs: dict = {}
    actions = sc._get_actions_dict(prefs)
    assert actions == {}
    # Verify the on-disk shape was materialized so a later write_atomic
    # produces something Vivaldi will load.
    assert prefs == {"vivaldi": {"actions": [{}]}}


def test_get_actions_dict_handles_empty_list() -> None:
    prefs = {"vivaldi": {"actions": []}}
    actions = sc._get_actions_dict(prefs)
    assert actions == {}
    assert prefs["vivaldi"]["actions"] == [{}]


# ---------------------------------------------------------------------------
# plan_apply — shortcut apply path
# ---------------------------------------------------------------------------


def _make_prefs(actions_inner: dict | None = None) -> dict:
    """Synthesize a minimal prefs dict; tests then mutate via plan_apply."""
    return {
        "vivaldi": {
            "actions": [actions_inner if actions_inner is not None else {}]
        }
    }


def test_plan_apply_overrides_and_preserves_gestures(tmp_path: Path) -> None:
    """Updating a command's shortcuts must NOT clobber its gestures.
    Vivaldi treats both fields as user-configurable; we only manage
    one of them, so the other must survive untouched."""
    prefs_path = tmp_path / "Preferences"
    prefs = _make_prefs(
        {
            "COMMAND_CLOSE_TAB": {
                "shortcuts": ["meta+w"],
                "gestures": ["20"],
            }
        }
    )
    plan = sc.plan_apply(prefs_path, prefs, {"COMMAND_CLOSE_TAB": ["meta+w", "ctrl+f4"]})
    plan.apply_fn(prefs)

    entry = prefs["vivaldi"]["actions"][0]["COMMAND_CLOSE_TAB"]
    assert entry["shortcuts"] == ["meta+w", "ctrl+f4"]
    assert entry["gestures"] == ["20"], "gestures field must survive a shortcut rewrite"


def test_plan_apply_snapshots_originals_on_first_apply(tmp_path: Path) -> None:
    """The first apply that manages a command must capture its CURRENT
    shortcuts in the state payload — that snapshot is what later applies
    use to restore the original when the command is dropped from the
    config."""
    prefs_path = tmp_path / "Preferences"
    prefs = _make_prefs({"COMMAND_CLOSE_TAB": {"shortcuts": ["meta+w"]}})
    plan = sc.plan_apply(prefs_path, prefs, {"COMMAND_CLOSE_TAB": ["meta+x"]})
    assert plan.state_payload == {"originals": {"COMMAND_CLOSE_TAB": ["meta+w"]}}


def test_plan_apply_does_not_re_snapshot_already_managed(tmp_path: Path) -> None:
    """Re-applying a config that re-manages an already-managed command
    must NOT overwrite the snapshot with the most-recent override —
    the snapshot's whole purpose is to remember the pre-management
    value, not the previous run's value."""
    prefs_path = tmp_path / "Preferences"
    state_path = sc._state_file(prefs_path)
    state_path.write_text(
        json.dumps({"originals": {"COMMAND_CLOSE_TAB": ["meta+w"]}})
    )
    prefs = _make_prefs({"COMMAND_CLOSE_TAB": {"shortcuts": ["meta+x"]}})

    plan = sc.plan_apply(prefs_path, prefs, {"COMMAND_CLOSE_TAB": ["meta+y"]})
    assert plan.state_payload == {"originals": {"COMMAND_CLOSE_TAB": ["meta+w"]}}


def test_plan_apply_restores_on_removal(tmp_path: Path) -> None:
    """Drop a command from config → plan must schedule a restore back
    to the snapshotted original, AND drop it from the state payload."""
    prefs_path = tmp_path / "Preferences"
    state_path = sc._state_file(prefs_path)
    state_path.write_text(
        json.dumps(
            {
                "originals": {
                    "COMMAND_CLOSE_TAB": ["meta+w"],
                    "COMMAND_NEW_TAB": ["meta+t"],
                }
            }
        )
    )
    prefs = _make_prefs(
        {
            "COMMAND_CLOSE_TAB": {"shortcuts": ["meta+x"]},
            "COMMAND_NEW_TAB": {"shortcuts": ["meta+u"]},
        }
    )

    # Empty config — drops both managed commands.
    plan = sc.plan_apply(prefs_path, prefs, {})
    plan.apply_fn(prefs)
    inner = prefs["vivaldi"]["actions"][0]
    assert inner["COMMAND_CLOSE_TAB"]["shortcuts"] == ["meta+w"]
    assert inner["COMMAND_NEW_TAB"]["shortcuts"] == ["meta+t"]
    assert plan.state_payload == {"originals": {}}


def test_plan_apply_rejects_unknown_command(tmp_path: Path) -> None:
    """Vivaldi seeds the full known-command list on first launch, so
    a command that isn't in `vivaldi.actions[0]` is almost always a
    typo. Reject loudly so the user sees the misspelling."""
    prefs_path = tmp_path / "Preferences"
    prefs = _make_prefs({"COMMAND_CLOSE_TAB": {"shortcuts": ["meta+w"]}})
    with pytest.raises(SystemExit, match="unknown Vivaldi command"):
        sc.plan_apply(prefs_path, prefs, {"COMMAND_NOT_REAL": ["x"]})


def test_plan_apply_diff_lines_shapes(tmp_path: Path) -> None:
    """Verify the diff line prefixes for the three transitions:
    + new (no current entry), ~ change, - restore."""
    prefs_path = tmp_path / "Preferences"
    state_path = sc._state_file(prefs_path)
    state_path.write_text(
        json.dumps({"originals": {"COMMAND_NEW_TAB": ["meta+t"]}})
    )
    prefs = _make_prefs(
        {
            "COMMAND_NEW_TAB": {"shortcuts": ["meta+u"]},
            "COMMAND_CLOSE_TAB": {"shortcuts": ["meta+w"]},
        }
    )

    plan = sc.plan_apply(
        prefs_path,
        prefs,
        {"COMMAND_CLOSE_TAB": ["meta+x"]},  # NEW_TAB dropped, CLOSE_TAB changed
    )
    joined = "\n".join(plan.diff_lines)
    assert "~ COMMAND_CLOSE_TAB" in joined
    assert "- COMMAND_NEW_TAB" in joined
    assert "restore original" in joined


# ---------------------------------------------------------------------------
# pwa validation (mirrors brave/pwa tests; just the entry-point smoke check)
# ---------------------------------------------------------------------------


def test_pwa_validate_accepts_url_list() -> None:
    out = pwa._validate_table({"urls": ["https://squoosh.app/"]})
    assert out == ["https://squoosh.app/"]


def test_pwa_validate_rejects_non_http() -> None:
    with pytest.raises(SystemExit, match="must start with http"):
        pwa._validate_table({"urls": ["javascript:alert(1)"]})
