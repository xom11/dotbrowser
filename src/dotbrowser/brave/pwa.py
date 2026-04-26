"""Manage Brave force-installed Progressive Web Apps via Chromium policy.

Brave (Chromium) honors the enterprise policy `WebAppInstallForceList`:
list URLs there and the browser fetches each manifest, downloads icons,
registers the app in `chrome://apps`, and emits a `.desktop` launcher.
Removing a URL from the list and restarting Brave causes it to uninstall
the app (deleting icons + the .desktop file) — a clean round-trip that
matches dotbrowser's "TOML is source of truth" model already used by
`[shortcuts]` and `[settings]`.

The policy file lives outside the user's profile (`/etc/brave/policies/
managed/dotbrowser-pwa.json` on Linux) and so requires `sudo` to write.
This is the only module in dotbrowser today that escalates privileges,
and it is also the only module whose persisted state is *not* a sidecar
next to `Preferences` — the policy file itself is the source of truth,
namespaced by filename so we never collide with other policies.

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
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotbrowser.brave.utils import Plan, find_preferences

NAMESPACE = "pwa"

# The filename is namespace-specific so dotbrowser never collides with
# policies installed by an MDM or other admin tooling. A user inspecting
# /etc/brave/policies/managed/ can tell at a glance what's ours.
POLICY_FILE = Path("/etc/brave/policies/managed/dotbrowser-pwa.json")
POLICY_KEY = "WebAppInstallForceList"

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
    """Bail out clearly if we're on a platform we haven't wired up yet.

    The schema and orchestration are platform-agnostic, but the policy
    file path and serialization format differ: macOS reads policies from
    a plist at `/Library/Managed Preferences/com.brave.Browser.plist`,
    not a JSON file under `/etc/`. Adding macOS later will be a small
    change to `POLICY_FILE` + the read/write helpers; until then, refuse
    rather than silently doing nothing.
    """
    if not sys.platform.startswith("linux"):
        sys.exit(
            f"error: [pwa] is implemented for Linux only at this time "
            f"(platform={sys.platform!r}). macOS support requires the "
            f"plist path /Library/Managed Preferences/com.brave.Browser.plist "
            f"and is planned next."
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


def _read_current_policy() -> dict[str, dict]:
    """Parse `POLICY_FILE` into `{url: entry_dict}`. Empty if no file.

    The file is world-readable (mode 0644 from `sudo install`), so this
    runs without escalation. A malformed file is treated as empty rather
    than fatal so a previous half-written run doesn't permanently brick
    the apply path — the next write will replace it.
    """
    if not POLICY_FILE.exists():
        return {}
    try:
        data = json.loads(POLICY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
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
    """Atomically replace `POLICY_FILE` with a JSON doc containing
    `entries`, escalating via sudo since the path lives in `/etc/`.

    Tests monkeypatch this whole function (rather than the `subprocess`
    calls) so they don't need sudo and don't need to mock argv parsing.
    """
    payload = {POLICY_KEY: entries}
    content = json.dumps(payload, indent=2) + "\n"
    subprocess.run(
        ["sudo", "mkdir", "-p", "-m", "0755", str(POLICY_FILE.parent)],
        check=True,
    )
    # `sudo tee` writes the file as root with the default umask. The
    # resulting mode is 0644, which is what Chromium expects for a
    # readable managed policy file.
    subprocess.run(
        ["sudo", "tee", str(POLICY_FILE)],
        input=content.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        check=True,
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
    file to read — the policy file at /etc/ IS the state, and it is
    world-readable. If the file does not exist or contains an empty
    list, emit `urls = []` with a note rather than erroring out, since
    "nothing managed" is a normal first-run state.
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

    d = sub.add_parser(
        "dump",
        help=f"emit URLs from {POLICY_FILE} as a `[pwa]` TOML table",
    )
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.set_defaults(func=cmd_dump)
