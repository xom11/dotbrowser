# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the CLI without installing (from repo root)
PYTHONPATH=src python -m dotbrowser <args>

# Or install editable then use the entry point
pip install -e .
dotbrowser brave shortcuts list

# Regenerate command_ids.py from upstream Chromium + brave-core headers
# (requires `gh` CLI authenticated)
python scripts/generate_brave_command_ids.py
```

Tests live under `tests/` and use `pytest` (install via `pip install -e ".[test]"`). Run with `pytest` from the repo root. The suite has three layers:

- `test_logic.py` / `test_platform.py` — pure-logic unit tests, run anywhere.
- `test_smoke.py` — invokes the CLI as a subprocess against the real on-disk Brave profile (read-only `list`/`dump`/`apply --dry-run`). Skipped if no profile is found.
- `test_apply_live.py` — synthesizes a fake `Preferences` in a tmp dir and runs the full apply/reset round-trip. Runs anywhere.

The `--kill-brave` path is intentionally NOT covered by pytest (it would interrupt the user's running browser). Verify it manually after code changes.

## Architecture

**CLI shape: `dotbrowser <BROWSER> [browser-options] <MODULE> <ACTION> [args]`**

The CLI is built from independent registration functions, not a monolithic argparse tree. Each layer's `register(subparsers)` mounts the next layer:

```
cli.py           → mounts brave/__init__.py::register
brave/__init__   → mounts brave/shortcuts.py::register (and adds --profile-root, --profile)
brave/shortcuts  → adds apply/dump/list actions and sets args.func
```

To add a new browser: create `src/dotbrowser/<name>/__init__.py` with `register(subparsers)` and call it from `cli.py::build_parser`. To add a new module under an existing browser: create the module file with `register(subparsers)` and call it from the browser's `__init__.py`. No central registry.

### Brave shortcuts: how the patching actually works

This is the load-bearing knowledge for `brave/shortcuts.py`:

1. **Storage location.** Brave keeps user-overridden accelerators in the profile `Preferences` JSON under `brave.accelerators`. Format: `{"<command_id_as_string>": ["Control+KeyC", ...], ...}`. There is also `brave.default_accelerators` which mirrors Brave's compiled-in defaults — used by us to reset a binding when the user removes it from their config.

2. **Why direct JSON patching is safe.** `brave.accelerators` is a `RegisterDictionaryPref` in regular `Preferences`, not in `Secure Preferences` — it has no HMAC integrity check. Verified against `brave/components/commands/browser/accelerator_pref_manager.cc` upstream. Do not move logic to keys that ARE in `Secure Preferences` without first solving the MAC problem.

3. **Why Brave must be closed.** Brave rewrites `Preferences` on exit from its in-memory `PrefService` (and on periodic flushes). Writing while Brave runs gets clobbered. `brave_running()` enforces this. The `--kill-brave` flag is the escape hatch: it captures Brave's main-process cmdline, SIGKILLs Brave (so it can't flush over our changes), applies, then relaunches it.

   Robustness notes for `--kill-brave`:
   - **Process detection is platform-specific.** `_brave_proc_name()` returns `"brave"` on Linux and `"Brave Browser"` (with a space) on macOS — that's the literal basename `pgrep -x` matches. On macOS, helper processes have different basenames (`Brave Browser Helper`, `Brave Browser Helper (GPU)`, ...) so `pgrep -x` already excludes them; on Linux they're all `brave` so the `--type=...` arg filter in `find_main_brave_cmdline` is what isolates the main process.
   - **Reading argv is platform-specific.** Linux: `/proc/<pid>/cmdline`. Chromium subprocesses overwrite their argv region (setproctitle-style), losing null separators — fall back to `shlex.split`. macOS has no `/proc`, so `_read_cmdline` shells out to `ps -o command= -p <pid>` and shlex-splits the result.
   - **Restart is platform-specific.** Linux: prefer `shutil.which("brave-browser")` over the captured argv[0] because the captured path is the inner binary (`/opt/brave.com/brave/brave`); launching that directly bypasses the wrapper's `CHROME_WRAPPER`/`PATH` setup and silently breaks default-browser registration and URL handlers. macOS: launch via `open -a "Brave Browser" --args ...` so Launch Services starts the .app bundle properly (re-registers URL handlers, restores dock state).

4. **Sidecar state file.** Brave's pref system garbage-collects unknown keys on launch, so we cannot store our "which IDs did dotbrowser write" record inside `Preferences`. Instead, `_state_file()` writes `<Preferences>.dotbrowser.shortcuts.json` next to it. This is what makes "remove a key from config → reset to default on next apply" work.

5. **Merge semantics.** `cmd_apply` is intentionally non-destructive: only IDs in the current config get overridden, plus IDs previously managed (via state file) but no longer in config get reset to their value from `brave.default_accelerators` (or popped if no default exists). Never wipe `brave.accelerators` entirely — it contains all of Brave's working bindings, not just user customizations.

6. **Write order.** `write_atomic()` (temp + `os.replace`) → `_set_managed_ids()` → reload + verify. State file is written AFTER `Preferences` so a crash mid-apply doesn't claim ownership of IDs we failed to write.

### Command-name mapping

`brave/command_ids.py` is **auto-generated** by `scripts/generate_brave_command_ids.py` from two upstream headers (`chromium/chromium/chrome/app/chrome_command_ids.h` and `brave/brave-core/app/brave_command_ids.h`). Do not hand-edit it — regenerate. Names are `IDC_FOO_BAR` lowercased to `foo_bar`. When two `IDC_` aliases share a command ID, first-wins (the script enforces this).

The brave-core source contains the comment "PLEASE DO NOT CHANGE THE VALUE OF EXISTING VALUE. That could break custom shortcut feature" — so command IDs are stable across Brave versions, which is what makes shipping a snapshot mapping viable.

## Constraints

- **Python 3.11+** required (uses stdlib `tomllib`). Code has a `tomli` fallback path but `requires-python = ">=3.11"` in pyproject.toml — keep these in sync.
- **Linux + macOS** are supported in `DEFAULT_PROFILE_ROOT` (chosen via `sys.platform` in `brave/__init__.py::_default_profile_root`). For Windows, `--profile-root` is required at the CLI; the helper returns `None` for unknown platforms so `--help` still works without crashing at import.
- **No runtime deps**. Stdlib only. Adding a dependency is a deliberate decision — prefer a stdlib solution first.
