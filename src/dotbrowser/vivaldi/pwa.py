"""Manage Vivaldi force-installed Progressive Web Apps via Chromium policy.

Vivaldi (Chromium) honors the enterprise policy `WebAppInstallForceList`
exactly the same way Brave does: list URLs there and the browser fetches
each manifest, downloads icons, registers the app in `chrome://apps`,
and emits a launcher (a `.desktop` file on Linux, an .app shim on macOS).
Removing a URL + restarting causes Vivaldi to uninstall the app.

The mechanism is identical to brave/pwa.py — only the on-disk paths
differ:

- Linux: `/etc/vivaldi/policies/managed/dotbrowser-pwa.json` (JSON,
  namespaced by filename so dotbrowser never collides with policies
  installed by an MDM).
- macOS: `/Library/Managed Preferences/com.vivaldi.Vivaldi.plist`
  (binary plist keyed by Vivaldi's bundle ID — there is exactly one such
  file per app, so the file is shared with any active MDM and we must
  read-modify-write the single `WebAppInstallForceList` key while
  preserving unrelated keys). Each write also kicks cfprefsd with
  `sudo killall cfprefsd` for the same reason as in the brave module:
  cfprefsd caches `CFPreferences*` lookups in memory and does not watch
  its backing files, so without the killall, Vivaldi silently launches
  with the cached "no policy" state on the next start.

Config schema (TOML), inside the unified `vivaldi.toml`:

    [pwa]
    urls = [
      "https://squoosh.app/",
      "https://app.element.io/",
    ]
"""
from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotbrowser.vivaldi.utils import Plan, find_preferences

NAMESPACE = "pwa"

POLICY_KEY = "WebAppInstallForceList"


def _default_policy_file() -> Path | None:
    """Per-platform location of the managed-policy file dotbrowser writes."""
    if sys.platform.startswith("linux"):
        return Path("/etc/vivaldi/policies/managed/dotbrowser-pwa.json")
    if sys.platform == "darwin":
        # Bundle-ID-named plist; cannot be namespaced by filename, so the
        # write path is read-modify-write to preserve unrelated MDM keys.
        return Path("/Library/Managed Preferences/com.vivaldi.Vivaldi.plist")
    return None


# Module-level so tests can monkeypatch it into a tmp path. None on
# unsupported platforms — `_check_platform_supported` errors out before
# anything that uses the path is reached.
POLICY_FILE = _default_policy_file()

# Defaults applied to every entry produced from `urls = [...]`. Same
# rationale as brave/pwa.py: `window` matches the address-bar Install
# button UX, and `create_desktop_shortcut` is a Linux/Windows hint
# Chromium ignores on macOS.
_DEFAULT_ENTRY = {
    "default_launch_container": "window",
    "create_desktop_shortcut": True,
}


def _check_platform_supported() -> None:
    if POLICY_FILE is None:
        sys.exit(
            f"error: [pwa] is not yet implemented on platform={sys.platform!r}. "
            f"Linux and macOS are supported."
        )


def _validate_table(raw: object) -> list[str]:
    """Pull the URL list out of a parsed `[pwa]` table.

    An empty table (`[pwa]` with no `urls` key, or `urls = []`) is the
    explicit "wipe my managed PWAs" gesture, mirroring `[settings]` /
    `[shortcuts]` semantics. A missing `[pwa]` header is handled at the
    orchestrator level (the module is never called).
    """
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


def _read_existing_payload() -> dict:
    """Return the full parsed policy file (whole dict), or `{}` if missing.

    On macOS this dict may carry unrelated MDM keys that we have to
    preserve when we round-trip — `_build_policy_payload` uses this to
    merge our key without clobbering those. On Linux the file is
    namespaced by filename so the result will only ever contain our key.

    Both formats are world-readable, so this runs without escalation.
    """
    if POLICY_FILE is None or not POLICY_FILE.exists():
        return {}
    try:
        if sys.platform == "darwin":
            with POLICY_FILE.open("rb") as f:
                data = plistlib.load(f)
        else:
            data = json.loads(POLICY_FILE.read_text())
    except (json.JSONDecodeError, plistlib.InvalidFileException, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_current_policy() -> dict[str, dict]:
    """Extract our managed `WebAppInstallForceList` entries as `{url: entry}`.

    Ignores any unrelated keys in the file (only relevant on macOS, where
    the plist is shared with potential MDM policies).
    """
    data = _read_existing_payload()
    entries = data.get(POLICY_KEY, [])
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("url"), str):
            out[e["url"]] = e
    return out


def _entry_for(url: str) -> dict[str, Any]:
    return {"url": url, **_DEFAULT_ENTRY}


def _build_policy_payload(entries: list[dict]) -> bytes:
    """Serialize entries into the on-disk policy bytes for this platform.

    Linux: the file is namespaced (`dotbrowser-pwa.json`) so we can own
    the whole document — write a fresh `{POLICY_KEY: entries}` JSON.

    macOS: the file is the per-bundle-ID plist shared with any active
    MDM, so we read-modify-write — merge `entries` into our key while
    preserving every other top-level key. Binary plist (`FMT_BINARY`)
    matches what `defaults` writes; users can inspect with `plutil -p`.
    """
    if sys.platform == "darwin":
        merged = dict(_read_existing_payload())
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


def _sudo_write_policy(entries: list[dict]) -> None:
    """Atomically replace `POLICY_FILE` with `entries`, via sudo.

    The path lives in a root-owned directory on both platforms
    (`/etc/vivaldi/policies/managed/` on Linux, `/Library/Managed
    Preferences/` on macOS) so escalation is unavoidable. The
    serialization differs per-platform but the install path doesn't —
    `mkdir -p` + `tee` works the same on both.

    Tests monkeypatch this whole function (rather than the `subprocess`
    calls) so they don't need sudo and don't need to mock argv parsing.
    """
    content = _build_policy_payload(entries)
    subprocess.run(
        ["sudo", "mkdir", "-p", "-m", "0755", str(POLICY_FILE.parent)],
        check=True,
    )
    subprocess.run(
        ["sudo", "tee", str(POLICY_FILE)],
        input=content,
        stdout=subprocess.DEVNULL,
        check=True,
    )

    if sys.platform == "darwin":
        # See brave/pwa.py for the full rationale: cfprefsd does not
        # watch its backing files for external mutations, so without
        # this kick a process that previously read the policy via
        # CFPreferences (Vivaldi on a prior launch, our own verify
        # path) keeps the cached "no value" answer and Vivaldi
        # silently launches without the policy applied. launchd
        # respawns cfprefsd within milliseconds.
        subprocess.run(
            ["sudo", "killall", "cfprefsd"],
            check=False,
            stderr=subprocess.DEVNULL,
        )


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    """Build a `Plan` for the `[pwa]` table.

    Pure with respect to Preferences: `apply_fn` and `verify_fn` are
    no-ops because the canonical state lives in the managed-policy file,
    not the profile JSON. The actual write happens in `external_apply_fn`,
    which the orchestrator runs after `write_atomic(prefs)` — so if the
    prefs side fails for an unrelated reason (e.g. settings refusal in a
    combined apply), we don't escalate to sudo for nothing.

    `prefs_path` is accepted for signature symmetry with the other
    modules but is unused: the policy file path is platform-derived,
    not profile-relative.
    """
    _check_platform_supported()
    _ = prefs_path  # signature symmetry with shortcuts/settings

    target_urls = _validate_table(raw_table)
    current = _read_current_policy()

    diff = diff_summary(current, target_urls)

    def apply_fn(_prefs: dict) -> None:
        """No-op: pwa state lives in /etc/ or /Library/, not in Preferences."""

    def verify_fn(_reloaded: dict) -> None:
        """No-op: see external_apply_fn for the read-back check."""

    def external_apply_fn() -> None:
        entries = [_entry_for(u) for u in target_urls]
        _sudo_write_policy(entries)
        actual = _read_current_policy()
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


def cmd_dump(args: argparse.Namespace) -> None:
    """Emit currently-managed URLs as a TOML `[pwa]` table.

    The platform-specific policy file IS the state, and it is
    world-readable on both Linux and macOS. If the file does not
    exist or contains an empty list, emit `urls = []` with a note
    rather than erroring out, since "nothing managed" is a normal
    first-run state.
    """
    _check_platform_supported()
    find_preferences(args.profile_root, args.profile)

    current = _read_current_policy()
    urls = sorted(current)

    lines = ["# Generated by `dotbrowser vivaldi pwa dump`", "[pwa]"]
    if urls:
        lines.append("urls = [")
        for u in urls:
            lines.append(f"  {json.dumps(u)},")
        lines.append("]")
    else:
        lines.append("urls = []")
        lines.append("")
        lines.append(f"# (no managed PWAs — {POLICY_FILE} does not exist or is empty)")
    out = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "pwa",
        help="inspect force-installed PWAs (apply lives at `vivaldi apply`)",
    )
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    _help_path = POLICY_FILE or "the managed-policy file"
    d = sub.add_parser(
        "dump",
        help=f"emit URLs from {_help_path} as a `[pwa]` TOML table",
    )
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.set_defaults(func=cmd_dump)
