"""Tests for dotbrowser edge — settings apply, init, and pwa."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from dotbrowser import edge as edge_pkg
from dotbrowser.edge import settings as st
from dotbrowser.edge import pwa


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_edge_profile(tmp_path: Path) -> Path:
    """Minimal Edge profile for testing."""
    profile = tmp_path / "Default"
    profile.mkdir()
    prefs = {
        "omnibox": {"prevent_url_elisions": False},
        "bookmark_bar": {"show_on_all_tabs": True},
    }
    (profile / "Preferences").write_text(json.dumps(prefs))
    return tmp_path


def _apply(profile_root: Path, config: Path, monkeypatch) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    args = argparse.Namespace(
        profile_root=profile_root,
        profile="Default",
        config=str(config),
        dry_run=False,
        kill_browser=False,
    )
    edge_pkg.cmd_apply(args)


# ---------------------------------------------------------------------------
# Settings apply
# ---------------------------------------------------------------------------


def test_settings_apply_writes_and_verifies(
    fake_edge_profile: Path, tmp_path: Path, monkeypatch
) -> None:
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        '[settings]\n'
        '"omnibox.prevent_url_elisions" = true\n'
        '"bookmark_bar.show_on_all_tabs" = false\n'
    )
    _apply(fake_edge_profile, cfg, monkeypatch)

    prefs = json.loads(
        (fake_edge_profile / "Default" / "Preferences").read_text()
    )
    assert prefs["omnibox"]["prevent_url_elisions"] is True
    assert prefs["bookmark_bar"]["show_on_all_tabs"] is False


def test_settings_apply_refuses_mac_protected_key(
    fake_edge_profile: Path, tmp_path: Path
) -> None:
    """MAC-protected keys must be refused."""
    prefs_path = fake_edge_profile / "Default" / "Preferences"
    prefs = json.loads(prefs_path.read_text())
    prefs["protection"] = {"macs": {"homepage": "somehash"}}
    prefs_path.write_text(json.dumps(prefs))

    with pytest.raises(SystemExit, match="MAC-protected"):
        st.plan_apply(
            prefs_path,
            json.loads(prefs_path.read_text()),
            {"homepage": "https://example.com"},
        )


def test_dry_run_does_not_write(
    fake_edge_profile: Path, tmp_path: Path, monkeypatch
) -> None:
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        '[settings]\n"omnibox.prevent_url_elisions" = true\n'
    )
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    args = argparse.Namespace(
        profile_root=fake_edge_profile,
        profile="Default",
        config=str(cfg),
        dry_run=True,
        kill_browser=False,
    )
    edge_pkg.cmd_apply(args)

    prefs = json.loads(
        (fake_edge_profile / "Default" / "Preferences").read_text()
    )
    # Should NOT have changed
    assert prefs["omnibox"]["prevent_url_elisions"] is False


def test_empty_config_errors(fake_edge_profile: Path, tmp_path: Path, monkeypatch) -> None:
    """A TOML with no recognized tables should error."""
    cfg = tmp_path / "empty.toml"
    cfg.write_text("# nothing here\n")
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    args = argparse.Namespace(
        profile_root=fake_edge_profile,
        profile="Default",
        config=str(cfg),
        dry_run=False,
        kill_browser=False,
    )
    with pytest.raises(SystemExit, match="nothing to apply"):
        edge_pkg.cmd_apply(args)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def test_init_stdout() -> None:
    """Edge init should print a valid template."""
    r = subprocess.run(
        [sys.executable, "-m", "dotbrowser", "edge", "init"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert r.returncode == 0
    assert "[settings]" in r.stdout
    assert "# [pwa]" in r.stdout
    # Edge doesn't have [shortcuts]
    assert "[shortcuts]" not in r.stdout


def test_init_output_file(tmp_path: Path) -> None:
    dest = tmp_path / "my-edge.toml"
    r = subprocess.run(
        [sys.executable, "-m", "dotbrowser", "edge", "init", "-o", str(dest)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert r.returncode == 0
    content = dest.read_text(encoding="utf-8")
    assert "[settings]" in content
    assert "my-edge.toml" in content


# ---------------------------------------------------------------------------
# PWA apply
#
# Mirrors tests/test_pwa_apply.py but for Edge — exercises the
# Edge-specific policy paths so a typo in the policy dir, bundle id,
# or registry key would land here instead of silently in production.
# ---------------------------------------------------------------------------


pwa_supported = pytest.mark.skipif(
    not (sys.platform.startswith("linux") or sys.platform == "darwin" or sys.platform == "win32"),
    reason="pwa apply path is implemented for Linux, macOS and Windows",
)


def _read_policy_file(path: Path) -> dict:
    if sys.platform == "darwin":
        with path.open("rb") as f:
            return plistlib.load(f)
    return json.loads(path.read_text())


@pytest.fixture
def fake_edge_pwa_profile(tmp_path: Path) -> Path:
    """Minimal profile the orchestrator can load -- pwa never touches it."""
    profile = tmp_path / "Default"
    profile.mkdir()
    (profile / "Preferences").write_text(json.dumps({"some": "thing"}))
    return tmp_path


@pytest.fixture
def fake_edge_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Edge's managed-policy storage into tmp + neutralize sudo/admin.

    Mirrors the Brave fake_policy fixture but uses Edge's filename
    (``com.microsoft.edgemac.plist`` on macOS) so the platform-specific
    serializer runs against an Edge-shaped target. The plist filename
    is the part most likely to regress on a refactor.
    """
    if sys.platform == "darwin":
        fake_path = tmp_path / "policy" / "com.microsoft.edgemac.plist"
    else:
        fake_path = tmp_path / "policy" / "dotbrowser-pwa.json"

    if sys.platform == "win32":
        def fake_read_payload() -> dict:
            if not fake_path.exists():
                return {}
            try:
                return json.loads(fake_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}

        monkeypatch.setattr(pwa, "_read_existing_payload", fake_read_payload)

        def fake_sudo_write(entries: list[dict]) -> None:
            fake_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {pwa.POLICY_KEY: entries}
            fake_path.write_text(json.dumps(payload, indent=2))

        monkeypatch.setattr(pwa, "_sudo_write_policy", fake_sudo_write)

        import ctypes
        monkeypatch.setattr(ctypes.windll.shell32, "IsUserAnAdmin", lambda: 1)
    else:
        monkeypatch.setattr(pwa, "POLICY_FILE", fake_path)

        def fake_sudo_write(entries: list[dict]) -> None:
            fake_path.parent.mkdir(parents=True, exist_ok=True)
            fake_path.write_bytes(pwa._build_policy_payload(entries))

        monkeypatch.setattr(pwa, "_sudo_write_policy", fake_sudo_write)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if list(cmd[:3]) == ["sudo", "-n", "true"]:
                return subprocess.CompletedProcess(cmd, 0)
            if list(cmd[:2]) == ["sudo", "-v"]:
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *args, **kwargs)

        from dotbrowser._base import orchestrator as orch
        monkeypatch.setattr(orch.subprocess, "run", fake_run)
    return fake_path


@pwa_supported
def test_edge_pwa_first_apply_writes_policy_file(
    fake_edge_pwa_profile: Path,
    fake_edge_policy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        '[pwa]\n'
        'urls = ["https://squoosh.app/", "https://app.element.io/"]\n'
    )
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    assert fake_edge_policy.exists()
    data = _read_policy_file(fake_edge_policy)
    urls = sorted(e["url"] for e in data[pwa.POLICY_KEY])
    assert urls == ["https://app.element.io/", "https://squoosh.app/"]


@pwa_supported
def test_edge_pwa_reapply_is_noop(
    fake_edge_pwa_profile: Path,
    fake_edge_policy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    cfg = tmp_path / "edge.toml"
    cfg.write_text('[pwa]\nurls = ["https://squoosh.app/"]\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)
    mtime = fake_edge_policy.stat().st_mtime_ns
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)
    assert fake_edge_policy.stat().st_mtime_ns == mtime


@pwa_supported
def test_edge_pwa_remove_url_uninstalls(
    fake_edge_pwa_profile: Path,
    fake_edge_policy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        '[pwa]\n'
        'urls = ["https://squoosh.app/", "https://app.element.io/"]\n'
    )
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    cfg.write_text('[pwa]\nurls = ["https://squoosh.app/"]\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    data = _read_policy_file(fake_edge_policy)
    assert [e["url"] for e in data[pwa.POLICY_KEY]] == ["https://squoosh.app/"]


@pwa_supported
def test_edge_pwa_empty_table_wipes(
    fake_edge_pwa_profile: Path,
    fake_edge_policy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    cfg = tmp_path / "edge.toml"
    cfg.write_text('[pwa]\nurls = ["https://squoosh.app/"]\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    cfg.write_text('[pwa]\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    data = _read_policy_file(fake_edge_policy)
    assert data[pwa.POLICY_KEY] == []


@pwa_supported
def test_edge_pwa_missing_table_leaves_policy_alone(
    fake_edge_pwa_profile: Path,
    fake_edge_policy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    cfg = tmp_path / "edge.toml"
    cfg.write_text('[pwa]\nurls = ["https://squoosh.app/"]\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)
    before = fake_edge_policy.read_bytes()

    cfg.write_text('[settings]\n"omnibox.prevent_url_elisions" = true\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    assert fake_edge_policy.read_bytes() == before


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="bundle-id-keyed plist preservation is a macOS concern",
)
def test_edge_pwa_macos_preserves_unrelated_mdm_keys(
    fake_edge_pwa_profile: Path,
    fake_edge_policy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``com.microsoft.edgemac.plist`` is keyed by Edge's bundle id, so an
    MDM may already write other policy keys to the same file. dotbrowser
    must not clobber them when it touches ``WebAppInstallForceList``."""
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    fake_edge_policy.parent.mkdir(parents=True, exist_ok=True)
    seed = {
        "HomepageLocation": "https://intranet.example.com/",
        "URLBlocklist": ["example.com"],
    }
    with fake_edge_policy.open("wb") as f:
        plistlib.dump(seed, f, fmt=plistlib.FMT_BINARY)

    cfg = tmp_path / "edge.toml"
    cfg.write_text('[pwa]\nurls = ["https://squoosh.app/"]\n')
    _apply(fake_edge_pwa_profile, cfg, monkeypatch)

    data = _read_policy_file(fake_edge_policy)
    assert [e["url"] for e in data[pwa.POLICY_KEY]] == ["https://squoosh.app/"]
    assert data["HomepageLocation"] == "https://intranet.example.com/"
    assert data["URLBlocklist"] == ["example.com"]
