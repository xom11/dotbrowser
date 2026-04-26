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
dotbrowser brave apply examples/brave/all.toml

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
- `test_unified_apply.py` — cross-module orchestration: combined diff, single backup per apply, settings-refusal blocking shortcuts write, missing-vs-empty table semantics, and the three-namespace (shortcuts + settings + pwa) round-trip.
- `test_pwa_apply.py` — `[pwa]`-only end-to-end. Linux-skipped elsewhere. Redirects `pwa.POLICY_FILE` into `tmp_path` and stubs `_sudo_write_policy` + the orchestrator's `sudo -v` preflight, so the suite never touches `/etc/` or prompts for a password.

The `--kill-browser` path is intentionally NOT covered by pytest (it would interrupt the user's running browser). Verify it manually after code changes.

## Architecture

**CLI shape: `dotbrowser <BROWSER> [browser-options] <ACTION> [args]`**

`apply` is at the browser level — one command writes `[shortcuts]`, `[settings]` and `[pwa]` from a single TOML file in a single backup + write cycle. Per-module subcommands (`shortcuts`, `settings`, `pwa`) only host read-only inspection actions (`dump`, `list`).

```
cli.py           → mounts brave/__init__.py::register
brave/__init__   → adds `apply` action; mounts brave/shortcuts.py + brave/settings.py
                   + brave/pwa.py for read-only inspection. Holds the I/O cycle
                   (sudo-preflight, kill-browser, backup, write_atomic, run
                   external_apply_fns, write state files, verify, restart).
brave/shortcuts  → exposes `plan_apply(prefs_path, prefs, raw_table) -> Plan`
                   plus dump/list CLI actions.
brave/settings   → exposes `plan_apply(prefs_path, prefs, raw_table) -> Plan`
                   plus dump CLI action.
brave/pwa        → exposes `plan_apply(prefs_path, prefs, raw_table) -> Plan`
                   plus dump CLI action. Sets `external_apply_fn` to sudo-write
                   `/etc/brave/policies/managed/dotbrowser-pwa.json`; leaves
                   apply_fn/verify_fn/state_path empty since pwa state lives
                   outside the profile.
brave/utils      → shared helpers (process detection, kill/restart, write_atomic,
                   get_nested) and the `Plan` dataclass.
```

**The `Plan` dataclass is the contract between modules and the orchestrator.** Each module's `plan_apply()` is pure — validates the TOML table, reads existing state, computes the diff, and returns a `Plan` with `namespace`, `diff_lines`, `apply_fn(prefs)`, `verify_fn(reloaded)`, plus optional `state_path`/`state_payload` (for sidecar persistence) and optional `external_apply_fn` (for side effects outside `Preferences`, like pwa's policy file write). The orchestrator collects plans, prints the combined diff, runs a sudo preflight if any plan has an `external_apply_fn`, then in order: kills Brave (if needed), backs up Preferences, runs all `apply_fn`s against one in-memory dict, `write_atomic`, runs all `external_apply_fn`s, writes state sidecars, runs `verify_fn`s. This guarantees: one backup per apply, no partial writes if any module rejects, sudo prompts come *before* the kill so an auth failure doesn't strand the user with a dead browser, and a single kill-browser + restart cycle.

**TOML table semantics for unified apply:**
- **Missing table** (no `[settings]` header): module is skipped entirely. State file untouched. This is the safe default for users who only manage one namespace.
- **Empty table** (`[settings]` followed by nothing): all previously-managed entries are reset (popped or reverted to default). State file becomes empty. This is the explicit "wipe my managed entries" gesture. The same rule applies to `[shortcuts]`.

To add a new browser: create `src/dotbrowser/<name>/__init__.py` with `register(subparsers)` and call it from `cli.py::build_parser`. To add a new module under an existing browser: implement `plan_apply()` returning a `Plan`, register it in the browser's `_build_plans()`, and (optionally) add inspection actions via a `register(subparsers)` for `dump`/`list`. No central registry.

### Brave shortcuts: how the patching actually works

This is the load-bearing knowledge for `brave/shortcuts.py`:

1. **Storage location.** Brave keeps user-overridden accelerators in the profile `Preferences` JSON under `brave.accelerators`. Format: `{"<command_id_as_string>": ["Control+KeyC", ...], ...}`. There is also `brave.default_accelerators` which mirrors Brave's compiled-in defaults — used by us to reset a binding when the user removes it from their config.

2. **Why direct JSON patching is safe.** `brave.accelerators` is a `RegisterDictionaryPref` in regular `Preferences`, not in `Secure Preferences` — it has no HMAC integrity check. Verified against `brave/components/commands/browser/accelerator_pref_manager.cc` upstream. Do not move logic to keys that ARE in `Secure Preferences` without first solving the MAC problem.

3. **Why Brave must be closed.** Brave rewrites `Preferences` on exit from its in-memory `PrefService` (and on periodic flushes). Writing while Brave runs gets clobbered. `brave_running()` enforces this. The `--kill-browser` flag is the escape hatch: it captures Brave's main-process cmdline, SIGKILLs Brave (so it can't flush over our changes), applies, then relaunches it.

   Robustness notes for `--kill-browser`:
   - **Process detection is platform-specific.** `_brave_proc_name()` returns `"brave"` on Linux and `"Brave Browser"` (with a space) on macOS — that's the literal basename `pgrep -x` matches. On macOS, helper processes have different basenames (`Brave Browser Helper`, `Brave Browser Helper (GPU)`, ...) so `pgrep -x` already excludes them; on Linux they're all `brave` so the `--type=...` arg filter in `find_main_brave_cmdline` is what isolates the main process.
   - **Reading argv is platform-specific.** Linux: `/proc/<pid>/cmdline`. Chromium subprocesses overwrite their argv region (setproctitle-style), losing null separators — fall back to `shlex.split`. macOS has no `/proc`, so `_read_cmdline` shells out to `ps -o command= -p <pid>` and shlex-splits the result.
   - **Restart is platform-specific.** Linux: prefer `shutil.which("brave-browser")` over the captured argv[0] because the captured path is the inner binary (`/opt/brave.com/brave/brave`); launching that directly bypasses the wrapper's `CHROME_WRAPPER`/`PATH` setup and silently breaks default-browser registration and URL handlers. macOS: launch via `open -a "Brave Browser" --args ...` so Launch Services starts the .app bundle properly (re-registers URL handlers, restores dock state).

4. **Sidecar state file.** Brave's pref system garbage-collects unknown keys on launch, so we cannot store our "which IDs did dotbrowser write" record inside `Preferences`. Instead, `_state_file()` writes `<Preferences>.dotbrowser.shortcuts.json` next to it. This is what makes "remove a key from config → reset to default on next apply" work.

5. **Merge semantics.** `plan_apply` is intentionally non-destructive: only IDs in the current config get overridden, plus IDs previously managed (via state file) but no longer in config get reset to their value from `brave.default_accelerators` (or popped if no default exists). Never wipe `brave.accelerators` entirely — it contains all of Brave's working bindings, not just user customizations.

6. **Write order is owned by the orchestrator, not this module.** `plan_apply` returns a `Plan`; the orchestrator in `brave/__init__.py` does `write_atomic(prefs)` first, then writes each plan's state sidecar. State files are written AFTER `Preferences` so a crash mid-apply doesn't claim ownership of IDs we failed to write.

7. **Platform-specific super/cmd modifier rewrite.** Brave serializes the super/cmd key as `Command+` on macOS and `Meta+` on Linux/Windows. Writing the wrong spelling is silently destructive — Brave's parser drops the unrecognized modifier on launch, so `Meta+KeyR` written on macOS reduces to bare `KeyR` (a single-letter binding that fires while typing). `_normalize_keys` rewrites either spelling to the current platform's form before `plan_apply` resolves IDs, so the same TOML config is portable. The diff and `verify_fn` both compare against the normalized values, so the displayed diff matches what Brave will actually parse. Only `Meta+` ↔ `Command+` are translated; other modifiers (`Control+`, `Shift+`, `Alt+`) pass through.

### Brave settings: how MAC refusal works

This is the load-bearing knowledge for `brave/settings.py`:

1. **Why MAC refusal exists.** Many UI-relevant prefs in `Preferences` (e.g. `homepage`, `session.startup_urls`, `browser.show_home_button`, `default_search_provider_data.template_url_data`, `pinned_tabs`) are Chromium "tracked prefs": each has a sibling entry under `protection.macs.<dotted_path>` containing an HMAC-SHA256 over `(pref_path, serialized_value)`. At launch Brave recomputes the MAC and resets the value to default if it doesn't match. Writing the value without updating the MAC is silently destructive — the change vanishes on next launch.

2. **The check is profile-driven, not a hardcoded allowlist.** `_is_mac_protected(prefs, parts)` walks the user's actual `protection.macs` subtree. This avoids stale allowlists and works regardless of whether the key has been materialized in `Preferences` yet (the macs subtree is populated for every tracked pref the user has touched). Refusal is conservative: a parent dict of a tracked leaf is also refused, because writing the parent would clobber the tracked child.

3. **`protection.*` is always refused.** That whole subtree is Chromium's MAC bookkeeping. Even if a key under it isn't itself MAC-protected, writing there has no defined semantics for us.

4. **No default-mirror.** Unlike `brave.accelerators` (which has a `brave.default_accelerators` sibling we can read to revert a binding), there is no general "defaults" dict for arbitrary prefs. When a key is removed from the user's config, `_pop_value()` deletes the leaf — Brave then falls back to its compiled-in default on next read. That's the intended behavior; document it loudly because it differs from shortcuts.

5. **Sidecar state file.** `Preferences.dotbrowser.settings.json` (separate from the shortcuts one) tracks managed dotted-path keys. Same crash-safety story as shortcuts: orchestrator writes `Preferences` first, state file second.

6. **`dump` semantics.** With no args, dump emits currently-managed keys. With explicit keys, it emits those — useful for "what's the current value?" discovery before adding a key to a config. Missing keys appear as commented-out lines so the user knows we looked but didn't find them.

### Brave pwa: how force-install actually works

This is the load-bearing knowledge for `brave/pwa.py`:

1. **Mechanism is Chromium's enterprise `WebAppInstallForceList` policy.** Listing a URL there causes Brave on next launch to fetch the manifest, download icons, register the app in `chrome://apps`, and emit a `.desktop` launcher (Linux) at `~/.local/share/applications/brave-<app_id>-Default.desktop`. Removing the URL + restarting causes Brave to uninstall the app, delete the icons directory, and remove the .desktop file. We don't reimplement any of this — Brave does it for us. Verified end-to-end against Brave 147 on Linux: `chrome://web-app-internals` confirms `latest_install_source: "external policy"`, and the `RemoveInstallSourceJob` fires automatically on policy removal with `result: kAppRemoved`.

2. **The policy file is the state.** Unlike shortcuts/settings (which keep a sidecar at `<Preferences>.dotbrowser.<ns>.json`), pwa's persistence is the policy file itself. Filename `dotbrowser-pwa.json` namespaces it so we never collide with policies installed by an MDM. `Plan.state_path` is therefore left `None`; the orchestrator skips the sidecar write for this module.

3. **Why sudo, why preflight.** The policy directory is root-owned on both supported platforms (`/etc/brave/policies/managed/` on Linux, `/Library/Managed Preferences/` on macOS), so the policy write goes through `sudo tee`. The orchestrator runs `sudo -v` *before* killing Brave or backing up Preferences — if auth fails, nothing destructive happens. `_sudo_write_policy` is the single shell-out point and is what tests monkeypatch to skip sudo without losing apply-path coverage. The platform-specific *serialization* lives in `_build_policy_payload` (still exercised live in tests via the fake fixture); only the privileged install step is faked.

4. **app_id is computed by Brave, not by us.** Chromium hashes the manifest_id (which usually equals the start_url) into the 32-char a-p extension-id format. Predicting it from the install URL is messy because manifests can declare a custom `id` field and append `utm_*` query params, so we deliberately don't try. We give Brave URLs; it gives us app_ids back via its own state.

5. **`default_launch_container = "window"` matches the address-bar Install button.** That's the standalone PWA window experience users get from clicking the install icon. `create_desktop_shortcut = true` is meaningful only on Linux/Windows (Chromium itself ignores it on macOS); leaving it `true` is harmless cross-platform.

6. **Brave restart is required for both install AND uninstall.** Policies are loaded at startup only — there's no live-reload signal. The orchestrator's existing `--kill-browser` flag covers this; pwa adds no new restart machinery.

7. **macOS path is read-modify-write, Linux is replace-whole-file.** Linux's `dotbrowser-pwa.json` is namespaced by filename — Chromium merges all files in `/etc/brave/policies/managed/` so we can own ours completely. macOS only loads one plist per bundle ID (`/Library/Managed Preferences/com.brave.Browser.plist`), so the file is shared with any active MDM. `_build_policy_payload` on macOS reads the existing plist (or `{}`), replaces just our `WebAppInstallForceList` key, and re-serializes to binary plist (`FMT_BINARY` to match `defaults`). The user-level alternative (`~/Library/Preferences/com.brave.Browser.plist` via `defaults write`) was probed and rejected: `WebAppInstallForceList` is `scope: machine`, so values written there load as recommended (not mandatory) and the install-force-list handler ignores them. Don't move the macOS path back to user-level without re-running the probe and confirming Chromium has changed behavior.

8. **macOS-only: `sudo killall cfprefsd` is mandatory after every write.** cfprefsd caches `CFPreferences*` lookups in memory and does NOT watch its backing files for external mutations — it assumes it owns the write side. Writing via `sudo tee` bypasses cfprefsd entirely, so any process that previously queried `WebAppInstallForceList` (Brave on a prior launch, our own read-back via `pyobjc` if we used it) keeps seeing the old "no value cached" answer. Brave's policy_loader queries via cfprefsd at startup, so it silently launches without the policy applied. Killing cfprefsd forces a re-scan on the next query; launchd respawns it within milliseconds and other apps reconnect transparently. Discovered the hard way during e2e verification: the file was on disk, `CFPreferencesAppValueIsForced` returned True from a *fresh* Python process (cold cfprefsd cache), but Brave saw nothing because its earlier launch had primed cfprefsd with "no policy". Don't remove the killall step.

### Command-name mapping

`brave/command_ids.py` is **auto-generated** by `scripts/generate_brave_command_ids.py` from two upstream headers (`chromium/chromium/chrome/app/chrome_command_ids.h` and `brave/brave-core/app/brave_command_ids.h`). Do not hand-edit it — regenerate. Names are `IDC_FOO_BAR` lowercased to `foo_bar`. When two `IDC_` aliases share a command ID, first-wins (the script enforces this).

The brave-core source contains the comment "PLEASE DO NOT CHANGE THE VALUE OF EXISTING VALUE. That could break custom shortcut feature" — so command IDs are stable across Brave versions, which is what makes shipping a snapshot mapping viable.

## Constraints

- **Python 3.11+** required (uses stdlib `tomllib`). Code has a `tomli` fallback path but `requires-python = ">=3.11"` in pyproject.toml — keep these in sync.
- **Linux + macOS** are supported in `DEFAULT_PROFILE_ROOT` (chosen via `sys.platform` in `brave/__init__.py::_default_profile_root`). For Windows, `--profile-root` is required at the CLI; the helper returns `None` for unknown platforms so `--help` still works without crashing at import.
- **No runtime deps**. Stdlib only. Adding a dependency is a deliberate decision — prefer a stdlib solution first.
