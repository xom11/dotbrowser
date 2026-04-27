"""Shared PWA (Progressive Web App) logic for Chromium-based browsers.

All Chromium browsers honor the ``WebAppInstallForceList`` enterprise
policy.  This module provides the shared validation, diffing, and
I/O logic.  Browser-specific modules configure paths and provide thin
wrappers (so tests can monkeypatch module-level ``POLICY_FILE`` and
``_sudo_write_policy`` per browser).
"""
from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import winreg

from dotbrowser._base.utils import Plan, find_preferences

NAMESPACE = "pwa"
POLICY_KEY = "WebAppInstallForceList"

_DEFAULT_ENTRY = {
    "default_launch_container": "window",
    "create_desktop_shortcut": True,
}


@dataclass
class PwaConfig:
    """Browser-specific PWA policy paths."""

    browser_name: str
    linux_policy_path: str
    macos_plist_path: str
    windows_registry_key: str
    sandbox_checks: list[tuple[str, str, str]] = field(default_factory=list)
    # Each sandbox check is (path_substring, install_name, policy_dir)


def default_policy_file(cfg: PwaConfig) -> Path | None:
    if sys.platform.startswith("linux"):
        return Path(cfg.linux_policy_path)
    if sys.platform == "darwin":
        return Path(cfg.macos_plist_path)
    return None


def check_platform_supported(policy_file: Path | None) -> None:
    if sys.platform == "win32":
        return
    if policy_file is None:
        sys.exit(
            f"error: [pwa] is not yet implemented on platform={sys.platform!r}. "
            f"Linux, macOS and Windows are supported."
        )


def check_install_supported(cfg: PwaConfig, prefs_path: Path) -> None:
    """Refuse [pwa] on sandboxed installs that can't read the policy dir."""
    p = str(prefs_path)
    for substr, install_name, policy_dir in cfg.sandbox_checks:
        if substr in p:
            sys.exit(
                f"error: [pwa] is not supported on {install_name} (the sandbox "
                f"does not read {policy_dir}). Install {cfg.browser_name.title()} "
                f"from the official package for [pwa] support, or "
                f"remove the [pwa] table from your config."
            )


def validate_table(raw: object) -> list[str]:
    if not isinstance(raw, dict):
        sys.exit("error: [pwa] must be a table")
    extra = set(raw.keys()) - {"urls"}
    if extra:
        sys.exit(
            f"error: [pwa] has unsupported keys: {sorted(extra)}. "
            f"v1 only supports `urls = [...]`"
        )
    urls = raw.get("urls", [])
    if not isinstance(urls, list):
        sys.exit("error: [pwa] urls must be an array of strings")
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if not isinstance(u, str):
            sys.exit(f"error: [pwa] url entries must be strings, got {type(u).__name__}")
        if not u.startswith(("http://", "https://")):
            sys.exit(f"error: [pwa] invalid url {u!r} (must start with http:// or https://)")
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def entry_for(url: str) -> dict[str, Any]:
    return {"url": url, **_DEFAULT_ENTRY}


def read_windows_registry_payload(windows_registry_key: str) -> dict:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            windows_registry_key + "\\" + POLICY_KEY,
            0,
            winreg.KEY_READ,
        )
    except OSError:
        return {}
    entries: list[dict] = []
    try:
        i = 0
        while True:
            try:
                _name, value, vtype = winreg.EnumValue(key, i)
                if vtype == winreg.REG_SZ and value:
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, dict):
                            entries.append(parsed)
                    except json.JSONDecodeError:
                        pass
                i += 1
            except OSError:
                break
    finally:
        winreg.CloseKey(key)
    return {POLICY_KEY: entries} if entries else {}


def read_existing_payload(
    policy_file: Path | None,
    windows_registry_key: str,
) -> dict:
    if sys.platform == "win32":
        return read_windows_registry_payload(windows_registry_key)
    if policy_file is None or not policy_file.exists():
        return {}
    try:
        if sys.platform == "darwin":
            with policy_file.open("rb") as f:
                data = plistlib.load(f)
        else:
            data = json.loads(policy_file.read_text())
    except (json.JSONDecodeError, plistlib.InvalidFileException, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_current_policy(
    policy_file: Path | None,
    windows_registry_key: str,
) -> dict[str, dict]:
    data = read_existing_payload(policy_file, windows_registry_key)
    entries = data.get(POLICY_KEY, [])
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("url"), str):
            out[e["url"]] = e
    return out


def build_policy_payload(
    policy_file: Path | None,
    windows_registry_key: str,
    entries: list[dict],
) -> bytes:
    if sys.platform == "darwin":
        merged = dict(read_existing_payload(policy_file, windows_registry_key))
        merged[POLICY_KEY] = entries
        return plistlib.dumps(merged, fmt=plistlib.FMT_BINARY)
    payload = {POLICY_KEY: entries}
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def diff_summary(current: dict[str, dict], target_urls: list[str]) -> list[str]:
    target_set = set(target_urls)
    current_set = set(current)
    lines: list[str] = []
    for url in sorted(target_set - current_set):
        lines.append(f"  + {url}")
    for url in sorted(current_set - target_set):
        lines.append(f"  - {url} (uninstall)")
    return lines


def write_windows_registry(
    windows_registry_key: str,
    entries: list[dict],
) -> None:
    key_path = windows_registry_key + "\\" + POLICY_KEY
    parent = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        windows_registry_key,
        0,
        winreg.KEY_WRITE,
    )
    winreg.CloseKey(parent)
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, key_path)
    except FileNotFoundError:
        pass
    key = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        key_path,
        0,
        winreg.KEY_WRITE,
    )
    try:
        for i, entry_item in enumerate(entries, start=1):
            winreg.SetValueEx(key, str(i), 0, winreg.REG_SZ, json.dumps(entry_item))
    finally:
        winreg.CloseKey(key)


def sudo_write_policy(
    policy_file: Path | None,
    windows_registry_key: str,
    entries: list[dict],
) -> None:
    """Write policy entries via the platform-specific privileged path."""
    if sys.platform == "win32":
        write_windows_registry(windows_registry_key, entries)
        return
    content = build_policy_payload(policy_file, windows_registry_key, entries)
    subprocess.run(
        ["sudo", "mkdir", "-p", "-m", "0755", str(policy_file.parent)],
        check=True,
    )
    subprocess.run(
        ["sudo", "tee", str(policy_file)],
        input=content,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    if sys.platform == "darwin":
        subprocess.run(
            ["sudo", "killall", "cfprefsd"],
            check=False,
            stderr=subprocess.DEVNULL,
        )


def plan_apply(
    cfg: PwaConfig,
    policy_file: Path | None,
    sudo_write_fn,
    read_policy_fn,
    prefs_path: Path,
    prefs: dict,
    raw_table: object,
) -> Plan:
    """Build a Plan for the [pwa] table.

    ``policy_file``, ``sudo_write_fn``, and ``read_policy_fn`` are
    passed in by the browser wrapper so that tests can monkeypatch
    the browser module's attributes and the changes are visible here.
    """
    check_platform_supported(policy_file)
    check_install_supported(cfg, prefs_path)

    target_urls = validate_table(raw_table)
    current = read_policy_fn()

    diff = diff_summary(current, target_urls)

    def apply_fn(_prefs: dict) -> None:
        pass

    def verify_fn(_reloaded: dict) -> None:
        pass

    def external_apply_fn() -> None:
        entries = [entry_for(u) for u in target_urls]
        sudo_write_fn(entries)
        actual = read_policy_fn()
        if set(actual) != set(target_urls):
            sys.exit(
                "error: pwa verification failed: policy file URL set does "
                f"not match config (wrote {sorted(target_urls)}, "
                f"file has {sorted(actual)})"
            )

    return Plan(
        namespace=NAMESPACE,
        diff_lines=diff,
        apply_fn=apply_fn,
        verify_fn=verify_fn,
        external_apply_fn=external_apply_fn,
    )


def cmd_dump(
    browser_name: str,
    policy_file: Path | None,
    windows_registry_key: str,
    read_policy_fn,
    args: argparse.Namespace,
) -> None:
    check_platform_supported(policy_file)
    find_preferences(args.profile_root, args.profile)

    current = read_policy_fn()
    urls = sorted(current)

    lines = [f"# Generated by `dotbrowser {browser_name} pwa dump`", "[pwa]"]
    if urls:
        lines.append("urls = [")
        for u in urls:
            lines.append(f"  {json.dumps(u)},")
        lines.append("]")
    else:
        lines.append("urls = []")
        lines.append("")
        if sys.platform == "win32":
            location = f"HKLM\\{windows_registry_key}\\{POLICY_KEY}"
        else:
            location = str(policy_file)
        lines.append(f"# (no managed PWAs -- {location} does not exist or is empty)")
    out = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def register(
    browser_name: str,
    policy_file: Path | None,
    windows_registry_key: str,
    read_policy_fn,
    cmd_dump_fn,
    subparsers: argparse._SubParsersAction,
) -> None:
    p = subparsers.add_parser(
        "pwa",
        help=f"inspect force-installed PWAs (apply lives at `{browser_name} apply`)",
    )
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    if sys.platform == "win32":
        _help_path = f"HKLM\\{windows_registry_key}"
    else:
        _help_path = policy_file or "the managed-policy file"
    d = sub.add_parser(
        "dump",
        help=f"emit URLs from {_help_path} as a `[pwa]` TOML table",
    )
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.set_defaults(func=cmd_dump_fn)
