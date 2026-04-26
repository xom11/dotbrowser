# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install from PyPI (end-user)
pip install dotbrowser

# Or install editable from a clone (development)
pip install -e ".[test]"

# Run the CLI without installing the entry point (from repo root)
PYTHONPATH=src python -m dotbrowser <args>

# Apply both [shortcuts] and [settings] from a single TOML file
dotbrowser brave apply examples/brave.toml

# Read-only inspection lives under each module
dotbrowser brave shortcuts list
dotbrowser brave shortcuts dump
dotbrowser brave settings dump

# Regenerate command_ids.py from upstream Chromium + brave-core headers
# (requires `gh` CLI authenticated)
python scripts/generate_brave_command_ids.py
```

## Releasing

Tag-driven via `.github/workflows/release.yml`. Steps:

1. Bump `version` in **both** `pyproject.toml` and `src/dotbrowser/__init__.py` (keep them in lockstep — there is no single source of truth yet, just two strings).
2. Commit on `main`.
3. Tag and push: `git tag v0.X.Y && git push && git push origin v0.X.Y`.
4. The `Publish to PyPI` workflow fires on the tag, builds sdist + wheel, runs `twine check`, and uploads using the `PYPI_API_TOKEN` repo secret. The token is project-scoped to `dotbrowser`. PyPI versions are immutable — once `0.X.Y` is uploaded you cannot reuse it; bump to `0.X.Y+1` for any fix, even a typo.

Tests live under `tests/` and use `pytest` (install via `pip install -e ".[test]"`). Run with `pytest` from the repo root. The suite has three layers:

- `test_logic.py` / `test_platform.py` — pure-logic unit tests, run anywhere. `test_platform.py` reloads `dotbrowser.brave.utils` (where the platform-aware process helpers live since the shortcuts/settings split).
- `test_smoke.py` — invokes the CLI as a subprocess against the real on-disk Brave profile (read-only `list`/`dump`/`apply --dry-run`). Skipped if no profile is found.
- `test_apply_live.py` — synthesizes a fake `Preferences` and exercises the unified apply path with a `[shortcuts]`-only TOML.
- `test_settings_apply.py` — same idea but `[settings]`-only; covers apply/refuse-MAC/drop-key, including the `protection.macs` parent-of-tracked-leaf refusal.
- `test_unified_apply.py` — cross-module orchestration: combined diff, single backup per apply, settings-refusal blocking shortcuts write, and missing-vs-empty table semantics.

The `--kill-brave` path is intentionally NOT covered by pytest (it would interrupt the user's running browser). Verify it manually after code changes.

## Architecture

**CLI shape: `dotbrowser <BROWSER> [browser-options] <ACTION> [args]`**

`apply` is at the browser level — one command writes both `[shortcuts]` and `[settings]` from a single TOML file in a single backup + write cycle. Per-module subcommands (`shortcuts`, `settings`) only host read-only inspection actions (`dump`, `list`).

```
cli.py           → mounts brave/__init__.py::register
brave/__init__   → adds `apply` action; mounts brave/shortcuts.py + brave/settings.py
                   for read-only inspection. Holds the I/O cycle (kill-brave,
                   backup, write_atomic, write state files, verify, restart).
brave/shortcuts  → exposes `plan_apply(prefs_path, prefs, raw_table) -> Plan`
                   plus dump/list CLI actions.
brave/settings   → exposes `plan_apply(prefs_path, prefs, raw_table) -> Plan`
                   plus dump CLI action.
brave/utils      → shared helpers (process detection, kill/restart, write_atomic,
                   get_nested) and the `Plan` dataclass.
```

**The `Plan` dataclass is the contract between modules and the orchestrator.** Each module's `plan_apply()` is pure — validates the TOML table, reads its sidecar state file, computes the diff, and returns a `Plan` with: `namespace`, `diff_lines`, `state_path`, `state_payload`, `apply_fn(prefs)`, `verify_fn(reloaded)`. The orchestrator collects plans, prints the combined diff, and runs all `apply_fn`s against one in-memory `Preferences` dict before a single `write_atomic`. State sidecars are written after `Preferences`, then `verify_fn`s run against the reloaded prefs. This guarantees: one backup per apply, no partial writes if any module rejects, and a single kill-brave + restart cycle.

**TOML table semantics for unified apply:**
- **Missing table** (no `[settings]` header): module is skipped entirely. State file untouched. This is the safe default for users who only manage one namespace.
- **Empty table** (`[settings]` followed by nothing): all previously-managed entries are reset (popped or reverted to default). State file becomes empty. This is the explicit "wipe my managed entries" gesture. The same rule applies to `[shortcuts]`.

To add a new browser: create `src/dotbrowser/<name>/__init__.py` with `register(subparsers)` and call it from `cli.py::build_parser`. To add a new module under an existing browser: implement `plan_apply()` returning a `Plan`, register it in the browser's `_build_plans()`, and (optionally) add inspection actions via a `register(subparsers)` for `dump`/`list`. No central registry.

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

5. **Merge semantics.** `plan_apply` is intentionally non-destructive: only IDs in the current config get overridden, plus IDs previously managed (via state file) but no longer in config get reset to their value from `brave.default_accelerators` (or popped if no default exists). Never wipe `brave.accelerators` entirely — it contains all of Brave's working bindings, not just user customizations.

6. **Write order is owned by the orchestrator, not this module.** `plan_apply` returns a `Plan`; the orchestrator in `brave/__init__.py` does `write_atomic(prefs)` first, then writes each plan's state sidecar. State files are written AFTER `Preferences` so a crash mid-apply doesn't claim ownership of IDs we failed to write.

### Brave settings: how MAC refusal works

This is the load-bearing knowledge for `brave/settings.py`:

1. **Why MAC refusal exists.** Many UI-relevant prefs in `Preferences` (e.g. `homepage`, `session.startup_urls`, `browser.show_home_button`, `default_search_provider_data.template_url_data`, `pinned_tabs`) are Chromium "tracked prefs": each has a sibling entry under `protection.macs.<dotted_path>` containing an HMAC-SHA256 over `(pref_path, serialized_value)`. At launch Brave recomputes the MAC and resets the value to default if it doesn't match. Writing the value without updating the MAC is silently destructive — the change vanishes on next launch.

2. **The check is profile-driven, not a hardcoded allowlist.** `_is_mac_protected(prefs, parts)` walks the user's actual `protection.macs` subtree. This avoids stale allowlists and works regardless of whether the key has been materialized in `Preferences` yet (the macs subtree is populated for every tracked pref the user has touched). Refusal is conservative: a parent dict of a tracked leaf is also refused, because writing the parent would clobber the tracked child.

3. **`protection.*` is always refused.** That whole subtree is Chromium's MAC bookkeeping. Even if a key under it isn't itself MAC-protected, writing there has no defined semantics for us.

4. **No default-mirror.** Unlike `brave.accelerators` (which has a `brave.default_accelerators` sibling we can read to revert a binding), there is no general "defaults" dict for arbitrary prefs. When a key is removed from the user's config, `_pop_value()` deletes the leaf — Brave then falls back to its compiled-in default on next read. That's the intended behavior; document it loudly because it differs from shortcuts.

5. **Sidecar state file.** `Preferences.dotbrowser.settings.json` (separate from the shortcuts one) tracks managed dotted-path keys. Same crash-safety story as shortcuts: orchestrator writes `Preferences` first, state file second.

6. **`dump` semantics.** With no args, dump emits currently-managed keys. With explicit keys, it emits those — useful for "what's the current value?" discovery before adding a key to a config. Missing keys appear as commented-out lines so the user knows we looked but didn't find them.

### Command-name mapping

`brave/command_ids.py` is **auto-generated** by `scripts/generate_brave_command_ids.py` from two upstream headers (`chromium/chromium/chrome/app/chrome_command_ids.h` and `brave/brave-core/app/brave_command_ids.h`). Do not hand-edit it — regenerate. Names are `IDC_FOO_BAR` lowercased to `foo_bar`. When two `IDC_` aliases share a command ID, first-wins (the script enforces this).

The brave-core source contains the comment "PLEASE DO NOT CHANGE THE VALUE OF EXISTING VALUE. That could break custom shortcut feature" — so command IDs are stable across Brave versions, which is what makes shipping a snapshot mapping viable.

## Constraints

- **Python 3.11+** required (uses stdlib `tomllib`). Code has a `tomli` fallback path but `requires-python = ">=3.11"` in pyproject.toml — keep these in sync.
- **Linux + macOS** are supported in `DEFAULT_PROFILE_ROOT` (chosen via `sys.platform` in `brave/__init__.py::_default_profile_root`). For Windows, `--profile-root` is required at the CLI; the helper returns `None` for unknown platforms so `--help` still works without crashing at import.
- **No runtime deps**. Stdlib only. Adding a dependency is a deliberate decision — prefer a stdlib solution first.
