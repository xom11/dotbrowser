"""Manage Brave force-installed Progressive Web Apps via Chromium policy.

Brave (Chromium) honors the enterprise policy `WebAppInstallForceList`:
list URLs there and the browser fetches each manifest, downloads icons,
registers the app in `chrome://apps`, and emits a launcher (a `.desktop`
file on Linux, an .app shim under `~/Applications/Brave Apps.localized`
on macOS). Removing a URL from the list and restarting Brave causes it
to uninstall the app — a clean round-trip that matches dotbrowser's
"TOML is source of truth" model already used by `[shortcuts]` and
`[settings]`.

The policy file lives outside the user's profile and requires `sudo` to
write on both supported platforms:

- Linux: `/etc/brave/policies/managed/dotbrowser-pwa.json` (JSON,
  namespaced by filename so dotbrowser never collides with policies
  installed by an MDM).
- macOS: `/Library/Managed Preferences/com.brave.Browser.plist` (binary
  plist keyed by Brave's bundle ID — there is exactly one such file per
  app, so we *cannot* namespace by filename and must read-modify-write
  the single `WebAppInstallForceList` key while preserving any unrelated
  MDM-managed keys in the same plist). Each write also kicks cfprefsd
  with `sudo killall cfprefsd` so its in-memory cache picks up the new
  file; without that step Brave silently launches with the *previous*
  policy state because cfprefsd doesn't watch its backing files for
  external changes (it assumes ownership of writes via its XPC API).

The user-level macOS path (`~/Library/Preferences/com.brave.Browser.plist`
via `defaults write`) was tested and rejected: Chromium classifies
`WebAppInstallForceList` as `scope: machine`, so values written there
load as recommended (not mandatory) and the install-force-list handler
ignores them. Probe procedure recorded in CLAUDE.md.

This is the only module in dotbrowser today that escalates privileges,
and it is also the only module whose persisted state is *not* a sidecar
next to `Preferences` — the policy file itself is the source of truth.

Config schema (TOML), inside the unified `brave.toml`:

    [pwa]
    urls = [
      "https://squoosh.app/",
      "https://app.element.io/",
    ]

For v1 every entry uses defaults: `default_launch_container = "window"`
and `create_desktop_shortcut = true`. Per-URL overrides (custom_name,
launch container, etc.) are deferred — when added, a richer schema can
coexist with the simple `urls = [...]` list without breaking existing
configs.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotbrowser.brave.utils import Plan, find_preferences

NAMESPACE = "pwa"

POLICY_KEY = "WebAppInstallForceList"


def _default_policy_file() -> Path | None:
    """Per-platform location of the managed-policy file dotbrowser writes."""
    if sys.platform.startswith("linux"):
        return Path("/etc/brave/policies/managed/dotbrowser-pwa.json")
    if sys.platform == "darwin":
        # Bundle-ID-named plist; cannot be namespaced by filename, so the
        # write path is read-modify-write to preserve unrelated MDM keys.
        return Path("/Library/Managed Preferences/com.brave.Browser.plist")
    return None


# Module-level so tests can monkeypatch it into a tmp path. None on
# unsupported platforms — `_check_platform_supported` errors out before
# anything that uses the path is reached.
POLICY_FILE = _default_policy_file()

# Defaults applied to every entry produced from `urls = [...]`. The
# `default_launch_container` value `window` matches the UX users get when
# clicking "Install" from Brave's address bar (standalone window). The
# `create_desktop_shortcut` flag is ignored on macOS by Chromium itself,
# so leaving it `true` is harmless cross-platform.
_DEFAULT_ENTRY = {
    "default_launch_container": "window",
    "create_desktop_shortcut": True,
}


def _check_platform_supported() -> None:
    """Bail out clearly on platforms we haven't wired up yet.

    Linux + macOS share schema, orchestration, validation, and diff
    computation; they only diverge at serialization (JSON vs binary
    plist) and file path. Windows would need a new path under
    `HKLM\\Software\\Policies\\BraveSoftware\\Brave\\` (registry, not a
    file) — not yet implemented.
    """
    if POLICY_FILE is None:
        sys.exit(
            f"error: [pwa] is not yet implemented on platform={sys.platform!r}. "
            f"Linux and macOS are supported."
        )


def _check_install_supported(prefs_path: Path) -> None:
    """Refuse `[pwa]` on Snap and Flatpak — both run inside sandboxes
    that don't read `/etc/brave/policies/managed/`, so a sudo write to
    that path would silently have no effect on next browser launch.
    Profile path is the cleanest signal we have for which install
    method is in use; the user can still use shortcuts/settings on a
    sandboxed Brave, just not pwa.
    """
    p = str(prefs_path)
    if "/snap/brave/" in p:
        sys.exit(
            "error: [pwa] is not supported on Snap Brave (the sandbox does "
            "not read /etc/brave/policies/managed/). Install Brave from the "
            "official .deb (Debian/Ubuntu) or .rpm (Fedora/RHEL) repo for "
            "[pwa] support, or remove the [pwa] table from your config."
        )
    if "/.var/app/com.brave.Browser/" in p:
        sys.exit(
            "error: [pwa] is not supported on Flatpak Brave (the sandbox "
            "does not read /etc/brave/policies/managed/). Install Brave "
            "from the official .deb or .rpm repo for [pwa] support, or "
            "remove the [pwa] table from your config."
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

    Both formats are world-readable (mode 0644 / 0644 respectively), so
    this runs without escalation. A malformed file is treated as empty
    rather than fatal so a previous half-written run doesn't permanently
    brick the apply path — the next write will replace it.
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
    preserving every other top-level key. We use binary plist
    (`FMT_BINARY`) to match what `defaults` writes; users can inspect
    with `plutil -p <file>`.
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
    (`/etc/brave/policies/managed/` on Linux, `/Library/Managed
    Preferences/` on macOS) so escalation is unavoidable. The
    serialization differs per-platform but the install path doesn't —
    `mkdir -p` + `tee` works the same on both.

    Tests monkeypatch this whole function (rather than the `subprocess`
    calls) so they don't need sudo and don't need to mock argv parsing.
    The serialization in `_build_policy_payload` is still exercised live
    through the test fixture's fake.
    """
    content = _build_policy_payload(entries)
    subprocess.run(
        ["sudo", "mkdir", "-p", "-m", "0755", str(POLICY_FILE.parent)],
        check=True,
    )
    # `sudo tee` writes the file as root with the default umask. The
    # resulting mode is 0644, which is what both Chromium (Linux managed
    # policies) and cfprefsd (macOS managed preferences) expect.
    subprocess.run(
        ["sudo", "tee", str(POLICY_FILE)],
        input=content,
        stdout=subprocess.DEVNULL,
        check=True,
    )

    if sys.platform == "darwin":
        # cfprefsd caches `CFPreferences*` lookups in memory and does
        # NOT watch its backing files for external changes — it owns
        # the writes and assumes nobody else touches them. Writing the
        # plist via `tee` bypasses cfprefsd entirely, so any process
        # (Brave on its next launch, our own read-back verify) that
        # queries the policy via the CFPreferences API will still get
        # the previously-cached value (typically: "no value set"),
        # and Brave will silently launch without the policy applied.
        # Killing cfprefsd forces it to re-scan on the next query;
        # launchd respawns it transparently within milliseconds.
        # Both the root daemon and the per-user agent are cleared so
        # both Brave's policy_loader and our verify path get fresh
        # data. `check=False` because killall returns 1 when there's
        # nothing matching to kill (e.g. on a system that just booted
        # and cfprefsd hasn't been demand-started yet).
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

    `prefs_path` drives the install-method check (sandbox installs like
    Snap and Flatpak can't apply pwa policies); the policy file path
    itself is platform-derived, not profile-relative.
    """
    _check_platform_supported()
    _check_install_supported(prefs_path)

    target_urls = _validate_table(raw_table)
    current = _read_current_policy()

    diff = diff_summary(current, target_urls)

    def apply_fn(_prefs: dict) -> None:
        """No-op: pwa state lives in /etc/, not in Preferences."""

    def verify_fn(_reloaded: dict) -> None:
        """No-op: see external_apply_fn for the read-back check."""

    def external_apply_fn() -> None:
        entries = [_entry_for(u) for u in target_urls]
        _sudo_write_policy(entries)
        # Read-back verify: confirm the file parses to exactly the URL
        # set we asked for. Catches things like sudo silently replacing
        # the file with a stale cache, NFS write reordering, etc.
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

    Unlike `settings dump`/`shortcuts dump`, there is no sidecar state
    file to read — the platform-specific policy file IS the state, and
    it is world-readable on both Linux and macOS. If the file does not
    exist or contains an empty list, emit `urls = []` with a note rather
    than erroring out, since "nothing managed" is a normal first-run
    state.
    """
    _check_platform_supported()
    # `find_preferences` is invoked only to validate that the user has
    # given us a real profile (consistent UX with the other dump cmds).
    find_preferences(args.profile_root, args.profile)

    current = _read_current_policy()
    urls = sorted(current)

    lines = ["# Generated by `dotbrowser brave pwa dump`", "[pwa]"]
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
        help="inspect force-installed PWAs (apply lives at `brave apply`)",
    )
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    # `POLICY_FILE` is None on unsupported platforms; fall back to a
    # generic blurb so `--help` still works without crashing at import.
    _help_path = POLICY_FILE or "the managed-policy file"
    d = sub.add_parser(
        "dump",
        help=f"emit URLs from {_help_path} as a `[pwa]` TOML table",
    )
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.set_defaults(func=cmd_dump)
