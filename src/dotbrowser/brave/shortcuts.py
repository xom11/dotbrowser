"""Manage Brave custom keyboard shortcuts via a TOML config file.

Brave stores user-overridden accelerators in its profile `Preferences` JSON
under the `brave.accelerators` key. The values are NOT in the protected
`Secure Preferences` file, so direct patching does not trip MAC integrity
checks (verified against brave/brave-core source).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from dotbrowser.brave.command_ids import ID_TO_NAME, NAME_TO_ID

ACCELERATORS_KEY_PATH = ("brave", "accelerators")
DEFAULT_ACCELERATORS_KEY_PATH = ("brave", "default_accelerators")


def find_preferences(profile_root: Path, profile: str) -> Path:
    p = profile_root / profile / "Preferences"
    if not p.exists():
        sys.exit(f"error: Preferences not found at {p}")
    return p


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _brave_proc_name() -> str:
    """The exact basename `pgrep -x` matches against.

    On Linux the main binary is `brave` (the `brave-browser` wrapper exec's
    into it). On macOS the executable inside the .app bundle is literally
    `Brave Browser` with a space — Helper processes use a different name
    (`Brave Browser Helper`, `Brave Browser Helper (GPU)`, ...) so `pgrep -x`
    on the exact name already excludes them.
    """
    return "Brave Browser" if _is_macos() else "brave"


def brave_running() -> bool:
    try:
        subprocess.check_output(
            ["pgrep", "-x", _brave_proc_name()], stderr=subprocess.DEVNULL
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _brave_pids() -> list[str]:
    try:
        out = subprocess.check_output(
            ["pgrep", "-x", _brave_proc_name()], stderr=subprocess.DEVNULL
        )
        return out.decode().split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _read_cmdline(pid: str) -> list[str] | None:
    """Recover the command-line argv for a running Brave process.

    Linux: read `/proc/<pid>/cmdline`. Chromium subprocesses overwrite their
    argv region (setproctitle-style) and lose null separators, leaving a
    single space-joined string — fall back to `shlex.split` in that case.

    macOS: no `/proc`. `ps -o command= -p <pid>` returns the full command
    line as a single line, but the executable path itself contains
    unescaped spaces (`/Applications/Brave Browser.app/Contents/MacOS/Brave
    Browser`) so `shlex.split` would corrupt it. We return the line as a
    single-element list — that's enough for the "did we capture anything?"
    signal that drives restart, and the macOS restart path doesn't need the
    parsed argv anyway (it relaunches via `open -a "Brave Browser"`).
    """
    if _is_macos():
        try:
            out = subprocess.check_output(
                ["ps", "-o", "command=", "-p", pid],
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        line = out.decode("utf-8", "replace").strip()
        return [line] if line else None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError):
        return None
    parts = [a.decode("utf-8", "replace") for a in raw.rstrip(b"\0").split(b"\0")]
    if len(parts) == 1 and " " in parts[0]:
        return shlex.split(parts[0])
    return parts


def find_main_brave_cmdline() -> list[str] | None:
    """The main Brave process is the one without a `--type=...` arg
    (renderer/utility/gpu subprocesses all carry --type). On macOS the
    helper processes also have different basenames so `pgrep -x` already
    excludes them; the --type filter is still cheap insurance, applied as
    a substring check so it works against the un-tokenized macOS form."""
    for pid in _brave_pids():
        args = _read_cmdline(pid)
        if not args:
            continue
        if any("--type=" in a for a in args):
            continue
        return args
    return None


def kill_brave_and_wait(timeout: float = 5.0) -> None:
    subprocess.run(
        ["pkill", "-KILL", "-x", _brave_proc_name()], stderr=subprocess.DEVNULL
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not brave_running():
            return
        time.sleep(0.1)
    sys.exit(f"error: Brave still running after SIGKILL + {timeout}s wait")


def restart_brave(captured_cmdline: list[str]) -> list[str]:
    """Restart Brave the way the OS expects.

    Linux: prefer the `brave-browser` wrapper script in PATH. It sets
    `CHROME_WRAPPER` and fixes PATH for xdg utilities (default-browser
    registration, URL handlers); launching the inner binary directly
    silently breaks those.

    macOS: launch through `open -a "Brave Browser"` so Launch Services
    starts the .app bundle properly (re-registers URL handlers, restores
    dock state). Captured argv[1:] is forwarded via `--args` so any flags
    that were active stay active after restart.

    Returns the cmdline actually used (for logging).
    """
    if _is_macos():
        cmdline = ["open", "-a", "Brave Browser"]
        forwarded = captured_cmdline[1:]
        if forwarded:
            cmdline += ["--args", *forwarded]
    else:
        wrapper = shutil.which("brave-browser") or shutil.which("brave")
        if wrapper:
            cmdline = [wrapper, *captured_cmdline[1:]]
        else:
            cmdline = list(captured_cmdline)
    subprocess.Popen(
        cmdline,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return cmdline


def load_prefs(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def get_nested(d: dict, keys: tuple[str, ...]) -> dict:
    for k in keys:
        d = d.setdefault(k, {})
    return d


def load_config(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    raw = data.get("shortcuts", {})
    if not isinstance(raw, dict):
        sys.exit("error: [shortcuts] must be a table")
    out: dict[str, list[str]] = {}
    for name, value in raw.items():
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            sys.exit(f"error: shortcuts.{name} must be a list of strings")
        out[name] = value
    return out


def resolve_command_ids(shortcuts: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    unknown = []
    for name, keys in shortcuts.items():
        if name not in NAME_TO_ID:
            unknown.append(name)
            continue
        out[str(NAME_TO_ID[name])] = keys
    if unknown:
        sys.exit(
            "error: unknown command name(s): "
            + ", ".join(sorted(unknown))
            + "\nrun `dotbrowser brave shortcuts list` to see all known commands"
        )
    return out


def diff_summary(
    current: dict, target: dict, removed_ids: set[str]
) -> list[str]:
    lines = []
    for cid, keys in target.items():
        name = ID_TO_NAME.get(int(cid), f"<unknown:{cid}>")
        if cid not in current:
            lines.append(f"  + {name}: {keys}")
        elif current[cid] != keys:
            lines.append(f"  ~ {name}: {current[cid]} -> {keys}")
    for cid in removed_ids:
        name = ID_TO_NAME.get(int(cid), f"<unknown:{cid}>")
        lines.append(f"  - {name}: {current[cid]} (reset to default)")
    return lines


def write_atomic(path: Path, prefs: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(prefs, f, separators=(",", ":"))
    os.replace(tmp, path)


def _state_file(prefs_path: Path) -> Path:
    return prefs_path.with_name(prefs_path.name + ".dotbrowser.shortcuts.json")


def _get_managed_ids(prefs_path: Path) -> set[str]:
    state = _state_file(prefs_path)
    if not state.exists():
        return set()
    try:
        data = json.loads(state.read_text())
    except json.JSONDecodeError:
        return set()
    return set(data.get("managed_ids", []))


def _set_managed_ids(prefs_path: Path, ids: set[str]) -> None:
    state = _state_file(prefs_path)
    state.write_text(json.dumps({"managed_ids": sorted(ids, key=int)}, indent=2))


def cmd_apply(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    config = load_config(args.config)
    target = resolve_command_ids(config)

    prefs = load_prefs(prefs_path)
    current = dict(get_nested(prefs, ACCELERATORS_KEY_PATH))
    defaults = dict(get_nested(prefs, DEFAULT_ACCELERATORS_KEY_PATH))

    target_ids = set(target)
    config_managed_ids = _get_managed_ids(prefs_path)
    removed_ids = {cid for cid in (config_managed_ids - target_ids) if cid in current}

    diff = diff_summary(current, target, removed_ids)
    if not diff:
        print("no changes — Preferences already match config")
        return

    print(f"target: {prefs_path}")
    print("changes:")
    print("\n".join(diff))

    if args.dry_run:
        print("\n(dry-run, nothing written)")
        return

    saved_cmdline: list[str] | None = None
    if brave_running():
        if not args.kill_brave:
            sys.exit(
                "error: Brave is running. Close it first, or pass --kill-brave\n"
                "(Brave caches prefs in memory and overwrites the file on exit,\n"
                "so editing while running is unreliable. --kill-brave SIGKILLs\n"
                "Brave to prevent the flush, applies, then restarts it.)"
            )
        saved_cmdline = find_main_brave_cmdline()
        pids = _brave_pids()
        print(f"killing Brave (pids: {' '.join(pids)})")
        kill_brave_and_wait()

    backup = prefs_path.with_suffix(
        prefs_path.suffix + f".bak.{datetime.now():%Y%m%d-%H%M%S}"
    )
    shutil.copy2(prefs_path, backup)
    print(f"backup: {backup}")

    accels = get_nested(prefs, ACCELERATORS_KEY_PATH)
    for cid, keys in target.items():
        accels[cid] = keys
    for cid in removed_ids:
        if cid in defaults:
            accels[cid] = list(defaults[cid])
        else:
            accels.pop(cid, None)
    write_atomic(prefs_path, prefs)
    _set_managed_ids(prefs_path, target_ids)

    reloaded = load_prefs(prefs_path)
    reloaded_accels = get_nested(reloaded, ACCELERATORS_KEY_PATH)
    for cid, keys in target.items():
        if reloaded_accels.get(cid) != keys:
            sys.exit(f"error: verification failed for command {cid}")
    print("ok — applied and verified")

    if saved_cmdline:
        used = restart_brave(saved_cmdline)
        print(f"restarting Brave: {' '.join(used)}")
    elif args.kill_brave:
        print("Brave killed; could not capture original command line — restart manually.")


def cmd_dump(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    prefs = load_prefs(prefs_path)
    accels = get_nested(prefs, ACCELERATORS_KEY_PATH)
    defaults = get_nested(prefs, DEFAULT_ACCELERATORS_KEY_PATH)

    header = "# Generated by `dotbrowser brave shortcuts dump`"
    if not args.all:
        header += " (only entries that differ from defaults; pass --all to see every binding)"
    lines = [header, "[shortcuts]"]
    unknown_lines = []
    for cid, keys in sorted(accels.items(), key=lambda kv: int(kv[0])):
        if not args.all and defaults.get(cid) == keys:
            continue
        name = ID_TO_NAME.get(int(cid))
        keys_repr = "[" + ", ".join(json.dumps(k) for k in keys) + "]"
        if name:
            lines.append(f"{name} = {keys_repr}")
        else:
            unknown_lines.append(f"# unknown command id {cid} = {keys_repr}")
    if unknown_lines:
        lines.append("")
        lines.append("# IDs not recognized by this tool's mapping:")
        lines.extend(unknown_lines)
    out = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def cmd_list(args: argparse.Namespace) -> None:
    needle = (args.filter or "").lower()
    rows = [
        (name, cid)
        for name, cid in sorted(NAME_TO_ID.items())
        if needle in name
    ]
    width = max((len(n) for n, _ in rows), default=0)
    for name, cid in rows:
        print(f"{name:<{width}}  {cid}")
    print(f"\n{len(rows)} commands", file=sys.stderr)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("shortcuts", help="manage keyboard shortcuts")
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    a = sub.add_parser("apply", help="patch Preferences from a TOML config")
    a.add_argument("config", type=Path)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument(
        "--kill-brave",
        action="store_true",
        help="if Brave is running, SIGKILL it (so it can't flush in-memory "
        "prefs over our changes), apply, then restart it",
    )
    a.set_defaults(func=cmd_apply)

    d = sub.add_parser("dump", help="emit current shortcuts as TOML")
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.add_argument(
        "--all",
        action="store_true",
        help="dump every binding, not just user-customized ones",
    )
    d.set_defaults(func=cmd_dump)

    l = sub.add_parser("list", help="list known command names")
    l.add_argument("filter", nargs="?", help="substring filter")
    l.set_defaults(func=cmd_list)
