"""Tests for the Vivaldi prefs-schema layer.

Covers:

* schema discovery via the ``DOTBROWSER_VIVALDI_PREFS_DEF`` env var
  (the override path; the install-path candidates are exercised
  implicitly when running on a machine with Vivaldi installed but are
  not part of automated coverage),
* flattening of both shapes the schema uses (nested ``vivaldi.*`` tree
  and the chromium-style ``{kIdent: {path, type}}`` tables),
* coercion + validation -- enum string -> int, type mismatches,
  unknown-key warning suppression for keys already present in
  Preferences,
* the schema-aware ``plan_apply`` (in-process), and
* the ``settings search`` / ``settings describe`` subcommands via the
  CLI (subprocess so argparse wiring is real).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

from dotbrowser import vivaldi as vivaldi_pkg
from dotbrowser.vivaldi import schema as _schema
from dotbrowser.vivaldi import settings as vsettings

REPO_ROOT = Path(__file__).resolve().parents[1]

# A miniature schema covering one of each shape we care about.  Real
# prefs_definitions.json is ~25k lines; this fixture stays narrow so
# expectations are obvious from the test source alone.
FAKE_SCHEMA: dict = {
    "documentation": "test",
    "vivaldi": {
        "tabs": {
            "bar": {
                "position": {
                    "type": "enum",
                    "enum_values": {"top": 0, "left": 1, "right": 2, "bottom": 3},
                    "default": "top",
                    "description": "Tab Bar Position",
                },
                "width": {
                    "type": "integer",
                    "default": 180,
                    "description": "Vertical Tab Bar width",
                },
            },
            "minimize": {
                "type": "boolean",
                "default": False,
                "description": "Minimize tabs",
            },
        },
    },
    "chromium": {
        "kAcceptLanguages": {
            "path": "intl.accept_languages",
            "type": "string",
        },
    },
    "chromium_local": {
        "kMemorySaverEnabled": {
            "path": "performance_tuning.high_efficiency_mode.state",
            "type": "integer",
        },
    },
}


@pytest.fixture
def fake_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Point the loader at a temp ``prefs_definitions.json`` and clear cache.

    The cache lives at process scope (``functools.lru_cache``) so tests
    that swap schemas in/out have to clear it explicitly.  Yields the
    flat ``{key: def}`` map for assertion convenience.
    """
    path = tmp_path / "prefs_definitions.json"
    path.write_text(json.dumps(FAKE_SCHEMA))
    monkeypatch.setenv("DOTBROWSER_VIVALDI_PREFS_DEF", str(path))
    _schema.load_schema.cache_clear()
    flat = _schema.load_schema()
    assert flat is not None
    yield flat
    _schema.load_schema.cache_clear()


@pytest.fixture
def no_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Force ``load_schema`` to return ``None`` -- env var points at a
    nonexistent file, which short-circuits before any candidate paths.
    """
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("DOTBROWSER_VIVALDI_PREFS_DEF", str(missing))
    _schema.load_schema.cache_clear()
    assert _schema.load_schema() is None
    yield
    _schema.load_schema.cache_clear()


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

def test_loader_flattens_both_shapes(fake_schema: dict) -> None:
    # Vivaldi nested tree -- key prefixed with the top-level "vivaldi.".
    assert "vivaldi.tabs.bar.position" in fake_schema
    assert fake_schema["vivaldi.tabs.bar.position"]["type"] == "enum"
    # Chromium-style: dotted key comes from the entry's `path` field,
    # NOT the kIdent.
    assert "intl.accept_languages" in fake_schema
    assert "kAcceptLanguages" not in fake_schema
    assert "performance_tuning.high_efficiency_mode.state" in fake_schema


def test_loader_returns_none_when_schema_absent(no_schema: None) -> None:
    assert _schema.load_schema() is None
    # Lookup on None must not raise.
    assert _schema.lookup(None, "anything") is None


def test_loader_handles_corrupt_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "prefs_definitions.json"
    bad.write_text("{not valid json")
    monkeypatch.setenv("DOTBROWSER_VIVALDI_PREFS_DEF", str(bad))
    _schema.load_schema.cache_clear()
    try:
        # Loader should swallow JSON errors and treat schema as missing.
        assert _schema.load_schema() is None
    finally:
        _schema.load_schema.cache_clear()


# --------------------------------------------------------------------------
# Coercion + validation
# --------------------------------------------------------------------------

def test_enum_string_coerced_to_int(fake_schema: dict) -> None:
    target = {"vivaldi.tabs.bar.position": "left"}
    warnings, errors = _schema.coerce_and_validate(target, fake_schema)
    assert errors == []
    assert warnings == []
    # The mutation is the whole point: downstream sees the int Vivaldi
    # actually stores on disk.
    assert target == {"vivaldi.tabs.bar.position": 1}


def test_enum_int_in_range_passes(fake_schema: dict) -> None:
    target = {"vivaldi.tabs.bar.position": 2}
    warnings, errors = _schema.coerce_and_validate(target, fake_schema)
    assert errors == []
    assert warnings == []
    assert target == {"vivaldi.tabs.bar.position": 2}


def test_enum_unknown_string_errors(fake_schema: dict) -> None:
    target = {"vivaldi.tabs.bar.position": "middle"}
    _, errors = _schema.coerce_and_validate(target, fake_schema)
    assert len(errors) == 1
    assert "'middle' not recognised" in errors[0]
    assert "'top'" in errors[0] and "'bottom'" in errors[0]


def test_enum_int_out_of_range_errors(fake_schema: dict) -> None:
    target = {"vivaldi.tabs.bar.position": 9}
    _, errors = _schema.coerce_and_validate(target, fake_schema)
    assert len(errors) == 1
    assert "out of range" in errors[0]


def test_type_mismatch_boolean(fake_schema: dict) -> None:
    target = {"vivaldi.tabs.minimize": "yes"}
    _, errors = _schema.coerce_and_validate(target, fake_schema)
    assert len(errors) == 1
    assert "expects boolean" in errors[0]


def test_type_mismatch_integer(fake_schema: dict) -> None:
    target = {"vivaldi.tabs.bar.width": "180"}
    _, errors = _schema.coerce_and_validate(target, fake_schema)
    assert len(errors) == 1
    assert "expects integer" in errors[0]


def test_unknown_key_warns_when_not_in_prefs(fake_schema: dict) -> None:
    target = {"vivaldi.totally.unknown": True}
    warnings, errors = _schema.coerce_and_validate(target, fake_schema, current_prefs={})
    assert errors == []
    assert len(warnings) == 1
    assert "unknown setting key" in warnings[0]
    # Value is left intact -- "warn but write" is the contract.
    assert target == {"vivaldi.totally.unknown": True}


def test_unknown_key_silent_when_present_in_prefs(fake_schema: dict) -> None:
    """``vivaldi.tabs.vertical_tabs_enabled`` is a real, runtime-set Vivaldi
    pref that's *not* in prefs_definitions.json.  Warning would be a
    false positive on every apply, so we suppress when the path
    already resolves in ``Preferences``.
    """
    prefs = {"vivaldi": {"tabs": {"vertical_tabs_enabled": False}}}
    target = {"vivaldi.tabs.vertical_tabs_enabled": True}
    warnings, errors = _schema.coerce_and_validate(target, fake_schema, current_prefs=prefs)
    assert errors == []
    assert warnings == []


def test_schema_none_is_noop() -> None:
    target = {"x": "y", "vivaldi.tabs.bar.position": "left"}
    snapshot = dict(target)
    warnings, errors = _schema.coerce_and_validate(target, None)
    assert errors == []
    assert warnings == []
    assert target == snapshot


def test_chromium_key_validated(fake_schema: dict) -> None:
    target = {"intl.accept_languages": 5}
    _, errors = _schema.coerce_and_validate(target, fake_schema)
    assert len(errors) == 1
    assert "expects string" in errors[0]


# --------------------------------------------------------------------------
# Search + describe API
# --------------------------------------------------------------------------

def test_search_matches_key_and_description(fake_schema: dict) -> None:
    matches = _schema.search(fake_schema, "tab bar position")
    assert ("vivaldi.tabs.bar.position", fake_schema["vivaldi.tabs.bar.position"]) in matches


def test_search_matches_enum_value_name(fake_schema: dict) -> None:
    # "bottom" appears only in enum_values, not in the key or description.
    matches = _schema.search(fake_schema, "bottom")
    keys = [k for k, _ in matches]
    assert "vivaldi.tabs.bar.position" in keys


def test_search_no_match_returns_empty(fake_schema: dict) -> None:
    assert _schema.search(fake_schema, "nonexistentterm") == []


def test_format_def_renders_enum(fake_schema: dict) -> None:
    defn = fake_schema["vivaldi.tabs.bar.position"]
    lines = _schema.format_def("vivaldi.tabs.bar.position", defn)
    blob = "\n".join(lines)
    assert "vivaldi.tabs.bar.position" in blob
    assert "type: enum" in blob
    assert "'top'=0" in blob and "'left'=1" in blob


# --------------------------------------------------------------------------
# Schema-aware plan_apply
# --------------------------------------------------------------------------

def _vivaldi_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "Default"
    profile.mkdir()
    prefs = {"vivaldi": {"tabs": {"bar": {}}}}
    (profile / "Preferences").write_text(json.dumps(prefs))
    return tmp_path


def test_plan_apply_coerces_enum(fake_schema: dict, tmp_path: Path) -> None:
    profile_root = _vivaldi_profile(tmp_path)
    prefs_path = profile_root / "Default" / "Preferences"
    prefs = json.loads(prefs_path.read_text())

    raw = {"vivaldi.tabs.bar.position": "left"}
    plan = vsettings.plan_apply(prefs_path, prefs, raw)

    # The diff and the apply both see the coerced int.
    assert any("= 1" in line for line in plan.diff_lines), plan.diff_lines
    assert raw["vivaldi.tabs.bar.position"] == 1


def test_plan_apply_aborts_on_invalid_enum(
    fake_schema: dict, tmp_path: Path
) -> None:
    profile_root = _vivaldi_profile(tmp_path)
    prefs_path = profile_root / "Default" / "Preferences"
    prefs = json.loads(prefs_path.read_text())

    raw = {"vivaldi.tabs.bar.position": "middle"}
    with pytest.raises(SystemExit) as exc:
        vsettings.plan_apply(prefs_path, prefs, raw)
    msg = str(exc.value)
    assert "schema validation" in msg
    assert "'middle'" in msg


def test_plan_apply_propagates_warnings(
    fake_schema: dict, tmp_path: Path
) -> None:
    profile_root = _vivaldi_profile(tmp_path)
    prefs_path = profile_root / "Default" / "Preferences"
    prefs = json.loads(prefs_path.read_text())

    raw = {"vivaldi.totally.unknown": True}
    plan = vsettings.plan_apply(prefs_path, prefs, raw)
    assert any("unknown setting key" in w for w in plan.warnings)


def test_plan_apply_falls_back_when_schema_absent(
    no_schema: None, tmp_path: Path
) -> None:
    profile_root = _vivaldi_profile(tmp_path)
    prefs_path = profile_root / "Default" / "Preferences"
    prefs = json.loads(prefs_path.read_text())

    # Schema missing -> no validation, no coercion, no warnings; writes
    # whatever the user gave us.  Mirrors pre-schema behavior so users
    # without the schema file aren't worse off.
    raw = {"vivaldi.tabs.bar.position": "left"}
    plan = vsettings.plan_apply(prefs_path, prefs, raw)
    assert any("'left'" in line or '"left"' in line for line in plan.diff_lines)
    assert plan.warnings == []


# --------------------------------------------------------------------------
# CLI: search + describe
# --------------------------------------------------------------------------

def _run_cli(
    profile_root: Path, schema_path: Path | None, *extra: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    if schema_path is not None:
        env["DOTBROWSER_VIVALDI_PREFS_DEF"] = str(schema_path)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dotbrowser",
            "vivaldi",
            "--profile-root",
            str(profile_root),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_search_finds_key(tmp_path: Path) -> None:
    schema_path = tmp_path / "prefs_definitions.json"
    schema_path.write_text(json.dumps(FAKE_SCHEMA))
    profile = _vivaldi_profile(tmp_path)
    r = _run_cli(profile, schema_path, "settings", "search", "tab bar position")
    assert r.returncode == 0, r.stderr
    assert "vivaldi.tabs.bar.position" in r.stdout
    assert "type: enum" in r.stdout
    assert "'left'=1" in r.stdout


def test_cli_search_no_match_exits_nonzero(tmp_path: Path) -> None:
    schema_path = tmp_path / "prefs_definitions.json"
    schema_path.write_text(json.dumps(FAKE_SCHEMA))
    profile = _vivaldi_profile(tmp_path)
    r = _run_cli(profile, schema_path, "settings", "search", "xyzzyqwerty")
    assert r.returncode == 1
    assert "no settings match" in r.stderr


def test_cli_describe_shows_current_value(tmp_path: Path) -> None:
    schema_path = tmp_path / "prefs_definitions.json"
    schema_path.write_text(json.dumps(FAKE_SCHEMA))
    profile = tmp_path
    (profile / "Default").mkdir()
    (profile / "Default" / "Preferences").write_text(
        json.dumps({"vivaldi": {"tabs": {"bar": {"position": 1}}}})
    )
    r = _run_cli(profile, schema_path, "settings", "describe", "vivaldi.tabs.bar.position")
    assert r.returncode == 0, r.stderr
    assert "current: 1" in r.stdout


def test_cli_describe_unknown_key_errors(tmp_path: Path) -> None:
    schema_path = tmp_path / "prefs_definitions.json"
    schema_path.write_text(json.dumps(FAKE_SCHEMA))
    profile = _vivaldi_profile(tmp_path)
    r = _run_cli(profile, schema_path, "settings", "describe", "nonexistent.key")
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_cli_apply_coerces_and_writes_int(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full CLI round-trip: TOML with ``"left"`` lands in Preferences as ``1``."""
    schema_path = tmp_path / "prefs_definitions.json"
    schema_path.write_text(json.dumps(FAKE_SCHEMA))
    profile_root = _vivaldi_profile(tmp_path)
    cfg = tmp_path / "settings.toml"
    cfg.write_text('[settings]\n"vivaldi.tabs.bar.position" = "left"\n')

    monkeypatch.setattr(vivaldi_pkg, "vivaldi_running", lambda: False)

    args = argparse.Namespace(
        profile_root=profile_root,
        profile="Default",
        config=cfg,
        dry_run=False,
        expect_sha256=None,
        allow_http=False,
    )
    monkeypatch.setenv("DOTBROWSER_VIVALDI_PREFS_DEF", str(schema_path))
    _schema.load_schema.cache_clear()
    try:
        vivaldi_pkg.cmd_apply(args)
    finally:
        _schema.load_schema.cache_clear()

    final = json.loads((profile_root / "Default" / "Preferences").read_text())
    assert final["vivaldi"]["tabs"]["bar"]["position"] == 1
