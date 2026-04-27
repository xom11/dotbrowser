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

# Scaffold a starter config
dotbrowser brave init                    # stdout
dotbrowser brave init -o brave.toml      # write to file

# Apply [shortcuts], [settings] and [pwa] from a single TOML file
dotbrowser brave apply examples/brave/all.toml

# Read-only inspection lives under each module
dotbrowser brave shortcuts list
dotbrowser brave shortcuts dump
dotbrowser brave settings dump
dotbrowser brave settings blocked       # MAC-protected keys `apply` refuses
dotbrowser brave pwa dump

# Export everything user-customised into one round-trippable TOML
dotbrowser brave export                  # stdout
dotbrowser brave export -o brave.toml    # write to file
dotbrowser brave export --all-shortcuts  # include defaults too (Brave/Vivaldi)

# Same commands work for vivaldi, edge, and chrome
dotbrowser vivaldi init
dotbrowser vivaldi apply examples/vivaldi/all.toml
dotbrowser edge init
dotbrowser edge apply examples/edge/all.toml
dotbrowser chrome init
dotbrowser chrome apply examples/chrome/all.toml

# Regenerate command_ids.py from upstream Chromium + brave-core headers
# (requires `gh` CLI authenticated)
python scripts/generate_brave_command_ids.py
```

## Releasing

Tag-driven via `.github/workflows/release.yml`. Steps:

1. Bump `__version__` in `src/dotbrowser/__init__.py` -- that's the single source of truth (`pyproject.toml` reads it via `[tool.hatch.version]`).
2. Commit on `main`.
3. Tag and push: `git tag v0.X.Y && git push && git push origin v0.X.Y`.
4. The `Publish to PyPI` workflow fires on the tag, builds sdist + wheel, runs `twine check`, and uploads using the `PYPI_API_TOKEN` repo secret. The token is project-scoped to `dotbrowser`. PyPI versions are immutable — once `0.X.Y` is uploaded you cannot reuse it; bump to `0.X.Y+1` for any fix, even a typo.

Tests live under `tests/` and use `pytest` (install via `pip install -e ".[test]"`). Run with `pytest` from the repo root. The suite has three layers:

- `test_logic.py` / `test_platform.py` — pure-logic unit tests, run anywhere. `test_platform.py` patches `dotbrowser._base.process` (where the platform-aware `BrowserProcess` and `_read_cmdline` live) and reloads browser utils modules to test platform dispatch.
- `test_smoke.py` — invokes the CLI as a subprocess against the real on-disk Brave profile (read-only `list`/`dump`/`apply --dry-run`). Skipped if no profile is found.
- `test_apply_live.py` — synthesizes a fake `Preferences` and exercises the unified apply path with a `[shortcuts]`-only TOML.
- `test_settings_apply.py` — same idea but `[settings]`-only; covers apply/refuse-MAC/drop-key, including the `protection.macs` parent-of-tracked-leaf refusal.
- `test_unified_apply.py` — cross-module orchestration: combined diff, single backup per apply, settings-refusal blocking shortcuts write, missing-vs-empty table semantics, and the three-namespace (shortcuts + settings + pwa) round-trip.
- `test_pwa_apply.py` — `[pwa]`-only end-to-end. Runs on Linux, macOS, and Windows. Redirects `pwa.POLICY_FILE` into `tmp_path` (Linux/macOS) or monkeypatches registry read/write to a temp JSON file (Windows) and stubs the privilege preflight, so the suite never touches `/etc/`, `/Library/`, the registry, or prompts for credentials.
- `test_edge_apply.py` — Edge settings apply, MAC refusal, dry-run, empty-config error, and `init` command. Same pattern as the Brave apply tests but for Edge (settings + pwa only, no shortcuts).
- `test_chrome_apply.py` — Chrome settings apply, MAC refusal (covers both `Preferences.protection.macs` and `Secure Preferences.protection.macs`), dry-run, empty-config error, `init`, and the full pwa lifecycle. Mirrors `test_edge_apply.py`.
- `test_export.py` — `<browser> export` for all four browsers. Redirects each browser's `pwa.POLICY_FILE` and `_read_existing_payload` to a tmp JSON file (same pattern as `test_pwa_apply.py`) so reads are deterministic without touching `/etc/`, `/Library/`, or the registry. Verifies (a) Brave's `[shortcuts]` is a diff against `brave.default_accelerators` and `--all-shortcuts` lifts that filter, (b) `[pwa]` reflects the seeded policy file, (c) Edge/Chrome omit `[shortcuts]` entirely, (d) Vivaldi emits every command with non-empty bindings (no defaults mirror to diff against), (e) the exported TOML is parseable and round-trips through `plan_apply` as a no-op.

The `--kill-browser` path is intentionally NOT covered by pytest (it would interrupt the user's running browser). Verify it manually after code changes.

## Architecture

**CLI shape: `dotbrowser <BROWSER> [browser-options] <ACTION> [args]`**

Supported browsers: **Brave** (shortcuts + settings + pwa), **Vivaldi** (shortcuts + settings + pwa), **Edge** (settings + pwa), **Chrome** (settings + pwa). Edge and Chrome do not support custom keyboard shortcuts via Preferences.

`apply` is at the browser level -- one command writes `[shortcuts]`, `[settings]` and `[pwa]` from a single TOML file in a single backup + write cycle. `init` scaffolds a starter config. `export` is the inverse of `apply`: emits a round-trippable TOML capturing the namespaces that have a notion of "user customisation" -- `[shortcuts]` (diff vs the browser's defaults) and `[pwa]` (force-installed list). `[settings]` is intentionally absent from `export`: Chromium has no defaults table for arbitrary prefs, so "diff vs default" isn't computable from the profile alone -- the export header documents this and points users at `settings dump`/`settings blocked`. Per-module subcommands (`shortcuts`, `settings`, `pwa`) only host read-only inspection actions (`dump`, `list`).

### Shared base (`_base/`)

Most logic is shared across all Chromium-based browsers in `src/dotbrowser/_base/`:

```
_base/utils.py        -> Plan dataclass, find_preferences, load_prefs,
                         write_atomic, get_nested.
_base/process.py      -> BrowserProcess class: parameterized process
                         detection, kill, restart per platform. Each
                         browser creates one instance with its names/paths.
_base/settings.py     -> Full settings module logic (MAC refusal,
                         plan_apply, cmd_dump, cmd_blocked, register).
                         Browser modules pass browser_name for
                         user-facing strings.
_base/pwa.py          -> Full PWA logic (validation, diff, policy
                         read/write, plan_apply). Browser modules provide
                         PwaConfig with paths and keep module-level
                         POLICY_FILE / _sudo_write_policy for testability.
_base/orchestrator.py -> cmd_apply (TOML loading, preflight, kill, backup,
                         write, verify, restart), cmd_init, cmd_export
                         (browser-provided builders -> single TOML file),
                         register_browser.
```

### Per-browser modules

Each browser is a thin wrapper that configures `_base/`:

```
cli.py              -> mounts brave, vivaldi, edge via register()
brave/__init__      -> _default_profile_root(channel), _build_plans,
                       _INIT_TEMPLATE, _setup_brave_profile_args (adds
                       --channel + defers --profile-root default),
                       _normalize_brave_args (resolves profile_root from
                       channel post-parse), cmd_apply (passes callbacks
                       to _base orchestrator -- module-level for stable,
                       channel-specific BrowserProcess for beta/nightly),
                       cmd_init, register.
brave/shortcuts     -> Brave-specific (numeric command IDs, Meta+/Command+
                       rewrite). Not shared -- each browser's shortcut
                       format differs.
brave/settings      -> thin wrapper: delegates to _base/settings with
                       browser_name="brave".
brave/pwa           -> thin wrapper: configures PwaConfig with Brave paths,
                       keeps POLICY_FILE / _sudo_write_policy as patchable
                       module attrs, delegates to _base/pwa.
brave/utils         -> BROWSER_PROCESS config + backward-compat aliases
                       (brave_running, restart_brave, etc.).
brave/command_ids   -> auto-generated IDC_* -> numeric ID mapping.
```

Vivaldi follows the same pattern. Edge and Chrome are simpler (no shortcuts module — neither browser exposes a customizable accelerator API in `Preferences`).

**The `Plan` dataclass is the contract between modules and the orchestrator.** Each module's `plan_apply()` is pure -- validates the TOML table, reads existing state, computes the diff, and returns a `Plan` with `namespace`, `diff_lines`, `apply_fn(prefs)`, `verify_fn(reloaded)`, plus optional `state_path`/`state_payload` (for sidecar persistence) and optional `external_apply_fn` (for side effects outside `Preferences`, like pwa's policy file write). The orchestrator collects plans, prints the combined diff, runs a sudo preflight if any plan has an `external_apply_fn`, then in order: kills the browser (if needed), backs up Preferences, runs all `apply_fn`s against one in-memory dict, runs all `external_apply_fn`s (privileged side-effects like the pwa policy write), `write_atomic` (commits Preferences), writes state sidecars, runs `verify_fn`s. External writes run *before* `write_atomic` so a sudo/I/O failure leaves Preferences untouched -- the alternative ordering committed prefs first and could strand the user with shortcuts/settings applied but pwa silently un-applied. This guarantees: one backup per apply, no partial writes if any module rejects, sudo prompts come *before* the kill so an auth failure doesn't strand the user with a dead browser, and a single kill-browser + restart cycle.

**Process callbacks are resolved at call time** in each browser's `cmd_apply` wrapper (not captured at import time), so test monkeypatching of `brave_pkg.brave_running` etc. takes effect. This is why each browser re-exports its process functions in `__init__.py` and passes them to the shared orchestrator.

**Brave release channels (stable / beta / nightly).** `--channel` selects which Brave install to target. The default for `--profile-root` is deferred to runtime via `register_browser`'s `setup_profile_args` + `normalize_args` hooks (see `_normalize_brave_args` in `brave/__init__.py`); `cli.main()` runs the normalizer before dispatching `args.func`. Profile path: `Brave-Browser` → `Brave-Browser-Beta` / `Brave-Browser-Nightly` on every OS. Process management: macOS proc/app names are channel-distinct (`Brave Browser Beta`), Windows install dirs differ (`Brave-Browser-Beta\Application\brave.exe`), but **on Linux all channels share `proc_name = "brave"`** because each channel installs to `/opt/brave.com/brave{,-beta,-nightly}/` with the same inner binary basename — so `pgrep -x brave` cannot distinguish channels by name. For non-stable channels, `_make_browser_process` sets `linux_pid_filter = "/opt/brave.com/brave-{channel}/"`, which `BrowserProcess.pids()` uses to drop pids whose argv[0] doesn't match; `kill_and_wait` then `kill -KILL <pid>...` instead of `pkill -x brave`, so a beta apply doesn't kill the user's running stable Brave (and vice versa). Stable keeps the permissive `pgrep`-only path because Snap/Flatpak installs use other paths (`/snap/brave/...` / `/app/brave/...`) that a strict filter would falsely exclude — so a Linux stable user with stable + beta running simultaneously will still hit "running()" via either channel; that's the surviving known limitation. Snap/Flatpak only ship stable, so `_default_profile_root` skips those probes for non-stable channels and `_make_browser_process` zeros out `flatpak_app_id` so `restart()` doesn't try `flatpak run` for a beta/nightly install. For stable, `cmd_apply` keeps using the module-level `brave_running` etc. so test monkeypatching still works; for non-stable it builds a fresh `BrowserProcess` and uses its methods directly.

**TOML table semantics for unified apply:**
- **Missing table** (no `[settings]` header): module is skipped entirely. State file untouched. This is the safe default for users who only manage one namespace.
- **Empty table** (`[settings]` followed by nothing): all previously-managed entries are reset (popped or reverted to default). State file becomes empty. This is the explicit "wipe my managed entries" gesture. The same rule applies to `[shortcuts]`.

**`export` is intentionally narrower than `apply`.** `apply` accepts three namespaces; `export` only emits two. The omission of `[settings]` is a hard architectural constraint, not a TODO:

- **Brave `[shortcuts]`** can be diffed exactly because `brave.default_accelerators` mirrors the compiled-in defaults inside the profile -- so we filter to bindings where `current[id] != defaults[id]`. `--all-shortcuts` lifts the filter for users who want a full snapshot.
- **Vivaldi `[shortcuts]`** has no defaults mirror. The closest approximation is "commands with a non-empty `shortcuts` field", which still includes Vivaldi's compiled-in defaults. The export header explicitly notes this so users don't expect Brave-grade diffing.
- **Edge/Chrome** have no `[shortcuts]` namespace at all (no `Preferences` accelerator API), so their `export` skips it.
- **`[pwa]`** is naturally diff-shaped: the managed-policy file only contains URLs the user (or an MDM) put there. Note this only covers force-installed PWAs -- user-installed PWAs (clicking "Install" in the address bar) live in `Preferences` under `web_app_ids`/`web_apps` and are out of scope.
- **`[settings]` is excluded** because Chromium's defaults are compiled C++ values, not exposed via `Preferences`. Snapshotting a fresh profile to diff against would be heavy and fragile; a hardcoded defaults table would be brittle and version-dependent. The export's top-of-file comment points users at `settings dump <key>...` and `settings blocked` instead.

**Export contract: pure builders + a list dispatched in the orchestrator.** `cmd_export(args, *, browser_name, builders)` lives in `_base/orchestrator.py`. Each builder is a `(args, prefs_path, prefs) -> list[str] | None` callable; the orchestrator joins their output with the standard header. Each module exposes `build_dump_block(...)` (pure -- no I/O beyond reads, no stdout, no `args.output` handling) so both the per-namespace `cmd_dump` CLI and `cmd_export` reuse the same string-building logic. `cmd_export_fn` is wired into `register_browser` alongside `cmd_apply_fn`/`cmd_init_fn`/`cmd_restore_fn`; `export_has_shortcuts=True` adds the `--all-shortcuts` flag (Edge/Chrome leave it `False`).

### Adding a new browser

Thanks to `_base/`, adding a new Chromium browser requires ~150-250 lines:

1. Create `src/dotbrowser/<name>/__init__.py` -- define `_default_profile_root()`, `_build_plans()`, `_INIT_TEMPLATE`, wire `cmd_apply`/`cmd_init`/`cmd_export`/`register` to the `_base` orchestrator. (If the browser has multiple release channels like Brave, also pass `setup_profile_args` and `normalize_args` to `register_browser` -- see Brave's `_setup_brave_profile_args` and `_normalize_brave_args` for the pattern.)
2. Create `<name>/utils.py` -- one `BrowserProcess(...)` instance + backward-compat aliases.
3. Create `<name>/settings.py` -- 3-line wrapper delegating to `_base.settings` with browser name.
4. Create `<name>/pwa.py` -- `PwaConfig` with policy paths + thin wrappers for `POLICY_FILE`/`_sudo_write_policy` (for test monkeypatching) + `build_dump_block()` (so `cmd_export` can collect the `[pwa]` block without re-implementing it).
5. (Optional) Create `<name>/shortcuts.py` if the browser has a shortcut customization API. If you do, expose `build_dump_block(prefs, *, all_bindings, header_comment)` so the export wiring stays a one-liner.
6. Wire `cmd_export` in `<name>/__init__.py` with per-namespace builders (`_export_shortcuts`, `_export_pwa`); pass `cmd_export_fn=cmd_export` and `export_has_shortcuts=True/False` to `register_browser`.
7. Add `from dotbrowser.<name> import register` in `cli.py::build_parser`.
8. Add `examples/<name>/` configs and `tests/test_<name>_apply.py`. Cover `export` in `tests/test_export.py` (parametrised by browser).

### Brave shortcuts: how the patching actually works

This is the load-bearing knowledge for `brave/shortcuts.py`:

1. **Storage location.** Brave keeps user-overridden accelerators in the profile `Preferences` JSON under `brave.accelerators`. Format: `{"<command_id_as_string>": ["Control+KeyC", ...], ...}`. There is also `brave.default_accelerators` which mirrors Brave's compiled-in defaults — used by us to reset a binding when the user removes it from their config.

2. **Why direct JSON patching is safe.** `brave.accelerators` is a `RegisterDictionaryPref` in regular `Preferences`, not in `Secure Preferences` — it has no HMAC integrity check. Verified against `brave/components/commands/browser/accelerator_pref_manager.cc` upstream. Do not move logic to keys that ARE in `Secure Preferences` without first solving the MAC problem.

3. **Why Brave must be closed.** Brave rewrites `Preferences` on exit from its in-memory `PrefService` (and on periodic flushes). Writing while Brave runs gets clobbered. `brave_running()` enforces this. The `--kill-browser` flag is the escape hatch: it captures Brave's main-process cmdline, force-kills Brave (so it can't flush over our changes), applies, then relaunches it.

   Robustness notes for `--kill-browser`:
   - **Process detection is platform-specific.** `_brave_proc_name()` returns `"brave"` on Linux, `"Brave Browser"` (with a space) on macOS, and `"brave.exe"` on Windows. On Linux/macOS, `pgrep -x` matches this name. On macOS, helper processes have different basenames (`Brave Browser Helper`, `Brave Browser Helper (GPU)`, ...) so `pgrep -x` already excludes them; on Linux they're all `brave` so the `--type=...` arg filter in `find_main_brave_cmdline` is what isolates the main process. On Windows, `tasklist /FI "IMAGENAME eq brave.exe" /FO CSV /NH` replaces `pgrep`.
   - **Reading argv is platform-specific.** Linux: `/proc/<pid>/cmdline`. Chromium subprocesses overwrite their argv region (setproctitle-style), losing null separators — fall back to `shlex.split`. macOS has no `/proc`, so `_read_cmdline` shells out to `ps -o command= -p <pid>` and returns the line as a single-element list (no shlex-split — the executable path contains spaces). Windows: PowerShell `Get-CimInstance Win32_Process -Filter 'ProcessId=<pid>'` returns the command line as a single string; also returned as a single-element list to avoid Windows backslash parsing issues.
   - **Restart is platform-specific.** Linux: prefer `shutil.which("brave-browser")` over the captured argv[0] because the captured path is the inner binary (`/opt/brave.com/brave/brave`); launching that directly bypasses the wrapper's `CHROME_WRAPPER`/`PATH` setup and silently breaks default-browser registration and URL handlers. macOS: launch via `open -a "Brave Browser" --args ...` so Launch Services starts the .app bundle properly (re-registers URL handlers, restores dock state). Windows: launch `brave.exe` from the standard install location (`%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe`); falls back to the captured command line if the known path doesn't exist.

4. **Sidecar state file.** Brave's pref system garbage-collects unknown keys on launch, so we cannot store our "which IDs did dotbrowser write" record inside `Preferences`. Instead, `_state_file()` writes `<Preferences>.dotbrowser.shortcuts.json` next to it. This is what makes "remove a key from config → reset to default on next apply" work.

5. **Merge semantics.** `plan_apply` is intentionally non-destructive: only IDs in the current config get overridden, plus IDs previously managed (via state file) but no longer in config get reset to their value from `brave.default_accelerators` (or popped if no default exists). Never wipe `brave.accelerators` entirely — it contains all of Brave's working bindings, not just user customizations.

6. **Write order is owned by the orchestrator, not this module.** `plan_apply` returns a `Plan`; the orchestrator in `brave/__init__.py` does `write_atomic(prefs)` first, then writes each plan's state sidecar. State files are written AFTER `Preferences` so a crash mid-apply doesn't claim ownership of IDs we failed to write.

7. **Platform-specific super/cmd modifier rewrite.** Brave serializes the super/cmd key as `Command+` on macOS and `Meta+` on Linux/Windows. Writing the wrong spelling is silently destructive — Brave's parser drops the unrecognized modifier on launch, so `Meta+KeyR` written on macOS reduces to bare `KeyR` (a single-letter binding that fires while typing). `_normalize_keys` rewrites either spelling to the current platform's form before `plan_apply` resolves IDs, so the same TOML config is portable. The diff and `verify_fn` both compare against the normalized values, so the displayed diff matches what Brave will actually parse. Only `Meta+` ↔ `Command+` are translated; other modifiers (`Control+`, `Shift+`, `Alt+`) pass through.

### Settings: how MAC refusal works

This is the load-bearing knowledge for `_base/settings.py` (shared by all browsers):

1. **Why MAC refusal exists.** Many UI-relevant prefs in `Preferences` (e.g. `homepage`, `session.startup_urls`, `browser.show_home_button`, `default_search_provider_data.template_url_data`, `pinned_tabs`) are Chromium "tracked prefs": each has a sibling entry under `protection.macs.<dotted_path>` containing an HMAC-SHA256 over `(pref_path, serialized_value)`. At launch Brave recomputes the MAC and resets the value to default if it doesn't match. Writing the value without updating the MAC is silently destructive — the change vanishes on next launch.

2. **The check is profile-driven, not a hardcoded allowlist.** `_is_mac_protected(prefs, parts)` walks the user's actual `protection.macs` subtree. This avoids stale allowlists and works regardless of whether the key has been materialized in `Preferences` yet (the macs subtree is populated for every tracked pref the user has touched). Refusal is conservative: a parent dict of a tracked leaf is also refused, because writing the parent would clobber the tracked child.

3. **`protection.*` is always refused.** That whole subtree is Chromium's MAC bookkeeping. Even if a key under it isn't itself MAC-protected, writing there has no defined semantics for us.

4. **No default-mirror.** Unlike `brave.accelerators` (which has a `brave.default_accelerators` sibling we can read to revert a binding), there is no general "defaults" dict for arbitrary prefs. When a key is removed from the user's config, `_pop_value()` deletes the leaf — Brave then falls back to its compiled-in default on next read. That's the intended behavior; document it loudly because it differs from shortcuts.

5. **Sidecar state file.** `Preferences.dotbrowser.settings.json` (separate from the shortcuts one) tracks managed dotted-path keys. Same crash-safety story as shortcuts: orchestrator writes `Preferences` first, state file second.

6. **`dump` semantics.** With no args, dump emits currently-managed keys. With explicit keys, it emits those — useful for "what's the current value?" discovery before adding a key to a config. Missing keys appear as commented-out lines so the user knows we looked but didn't find them.

7. **MAC bookkeeping is read from BOTH ``Preferences`` and ``Secure Preferences``.** Brave/Vivaldi/Edge keep most tracked-pref MACs inside the regular `Preferences` file under `protection.macs.<dotted_path>`. Chrome puts most of its tracked-pref MACs in a *separate* file, `Secure Preferences`, alongside `Preferences`, under the same `protection.macs` shape. `_all_macs(prefs, prefs_path)` deep-merges the two `protection.macs` subtrees so `_is_mac_protected` and `_walk_mac_leaves` see a single union view; `_load_secure_prefs` returns `{}` when the sibling file is absent or unreadable, which preserves pre-existing behavior for browsers that don't use it. `cmd_blocked` also falls back to `Secure Preferences` for value lookup so users can see what's currently set in Chrome's split storage. Without this, `apply` would silently accept keys like `homepage` or `browser.show_home_button` for Chrome and Chrome would reset them on next launch.
8. **Brave Sync warning.** When `sync.has_setup_completed` is true and `[settings]` would write or remove keys, `plan_apply` attaches a non-fatal warning to the returned `Plan`. The orchestrator prints all `Plan.warnings` between the `target:` line and the diff sections. Rationale: synced prefs can be silently overwritten on Sync's next pulse, which looks like dotbrowser being broken when it isn't. The warning is conservative on purpose — `sync.has_setup_completed` stays true after sign-out, so we may warn slightly more often than strictly needed; that's the right side to err on. Most user-configurable prefs aren't actually synced (the commonly-synced ones — homepage, default search, startup URLs — are MAC-protected and already refused), so the warning is mostly defensive. Implemented via `_sync_enabled(browser_name, prefs)` which dispatches through `_SYNC_KEY_BY_BROWSER`. Brave and Edge share the Chromium key `sync.has_setup_completed`; Vivaldi has its own sync stack and uses `vivaldi.sync.has_setup_completed`. The Vivaldi key is best-effort (no upstream contract guarantees it stays stable across versions); if Vivaldi changes its schema the warning may fire when sync is off, which is acceptable -- false-positive beats false-negative for a defensive warning. An unknown browser falls back to the Chromium key.

### PWA: how force-install actually works

This is the load-bearing knowledge for `_base/pwa.py` and the per-browser `pwa.py` wrappers:

1. **Mechanism is Chromium's enterprise `WebAppInstallForceList` policy.** Listing a URL there causes Brave on next launch to fetch the manifest, download icons, register the app in `chrome://apps`, and emit a `.desktop` launcher (Linux) at `~/.local/share/applications/brave-<app_id>-Default.desktop`. Removing the URL + restarting causes Brave to uninstall the app, delete the icons directory, and remove the .desktop file. We don't reimplement any of this — Brave does it for us. Verified end-to-end against Brave 147 on Linux: `chrome://web-app-internals` confirms `latest_install_source: "external policy"`, and the `RemoveInstallSourceJob` fires automatically on policy removal with `result: kAppRemoved`.

2. **The policy file is the state.** Unlike shortcuts/settings (which keep a sidecar at `<Preferences>.dotbrowser.<ns>.json`), pwa's persistence is the policy file itself. Filename `dotbrowser-pwa.json` namespaces it so we never collide with policies installed by an MDM. `Plan.state_path` is therefore left `None`; the orchestrator skips the sidecar write for this module.

3. **Why elevated privileges, why preflight.** The policy directory is root-owned on Linux/macOS (`/etc/brave/policies/managed/` on Linux, `/Library/Managed Preferences/` on macOS), so the policy write goes through `sudo tee`. On Windows the policy lives in `HKLM\Software\Policies\BraveSoftware\Brave\WebAppInstallForceList` (admin-only for writes), so `winreg` writes require an elevated (Administrator) process. The orchestrator runs a privilege preflight (`sudo -v` on Linux/macOS, `ctypes.windll.shell32.IsUserAnAdmin()` on Windows) *before* killing Brave or backing up Preferences — if auth fails, nothing destructive happens. `_sudo_write_policy` is the single write point (dispatches to `_write_windows_registry` on Windows) and is what tests monkeypatch to skip elevation without losing apply-path coverage. The platform-specific *serialization* lives in `_build_policy_payload` (Linux/macOS) or `_write_windows_registry` (Windows); both are still exercised live in tests via the fake fixture.

4. **app_id is computed by Brave, not by us.** Chromium hashes the manifest_id (which usually equals the start_url) into the 32-char a-p extension-id format. Predicting it from the install URL is messy because manifests can declare a custom `id` field and append `utm_*` query params, so we deliberately don't try. We give Brave URLs; it gives us app_ids back via its own state.

5. **`default_launch_container = "window"` matches the address-bar Install button.** That's the standalone PWA window experience users get from clicking the install icon. `create_desktop_shortcut = true` is meaningful only on Linux/Windows (Chromium itself ignores it on macOS); leaving it `true` is harmless cross-platform.

6. **Brave restart is required for both install AND uninstall.** Policies are loaded at startup only — there's no live-reload signal. The orchestrator's existing `--kill-browser` flag covers this; pwa adds no new restart machinery.

7. **macOS path is read-modify-write, Linux is replace-whole-file, Windows is delete-recreate registry key.** Linux's `dotbrowser-pwa.json` is namespaced by filename — Chromium merges all files in `/etc/brave/policies/managed/` so we can own ours completely. macOS only loads one plist per bundle ID (`/Library/Managed Preferences/com.brave.Browser.plist`), so the file is shared with any active MDM. `_build_policy_payload` on macOS reads the existing plist (or `{}`), replaces just our `WebAppInstallForceList` key, and re-serializes to binary plist (`FMT_BINARY` to match `defaults`). The user-level alternative (`~/Library/Preferences/com.brave.Browser.plist` via `defaults write`) was probed and rejected: `WebAppInstallForceList` is `scope: machine`, so values written there load as recommended (not mandatory) and the install-force-list handler ignores them. Don't move the macOS path back to user-level without re-running the probe and confirming Chromium has changed behavior. Windows: `_write_windows_registry` deletes the `HKLM\...\WebAppInstallForceList` subkey (clearing stale numbered values) and recreates it with fresh entries. Chromium on Windows reads list policies as numbered REG_SZ values ("1", "2", …) each containing a JSON string for one entry.

8. **macOS-only: `sudo killall cfprefsd` is mandatory after every write.** cfprefsd caches `CFPreferences*` lookups in memory and does NOT watch its backing files for external mutations — it assumes it owns the write side. Writing via `sudo tee` bypasses cfprefsd entirely, so any process that previously queried `WebAppInstallForceList` (Brave on a prior launch, our own read-back via `pyobjc` if we used it) keeps seeing the old "no value cached" answer. Brave's policy_loader queries via cfprefsd at startup, so it silently launches without the policy applied. Killing cfprefsd forces a re-scan on the next query; launchd respawns it within milliseconds and other apps reconnect transparently. Discovered the hard way during e2e verification: the file was on disk, `CFPreferencesAppValueIsForced` returned True from a *fresh* Python process (cold cfprefsd cache), but Brave saw nothing because its earlier launch had primed cfprefsd with "no policy". Don't remove the killall step.

### Command-name mapping

`brave/command_ids.py` is **auto-generated** by `scripts/generate_brave_command_ids.py` from two upstream headers (`chromium/chromium/chrome/app/chrome_command_ids.h` and `brave/brave-core/app/brave_command_ids.h`). Do not hand-edit it — regenerate. Names are `IDC_FOO_BAR` lowercased to `foo_bar`. When two `IDC_` aliases share a command ID, first-wins (the script enforces this).

The brave-core source contains the comment "PLEASE DO NOT CHANGE THE VALUE OF EXISTING VALUE. That could break custom shortcut feature" — so command IDs are stable across Brave versions, which is what makes shipping a snapshot mapping viable.

## Constraints

- **Python 3.11+** required (uses stdlib `tomllib`). Code has a `tomli` fallback path but `requires-python = ">=3.11"` in pyproject.toml — keep these in sync.
- **Linux, macOS, and Windows** are supported. Each browser's `_default_profile_root()` auto-detects the profile path per platform. For unsupported platforms (BSD, etc.), `--profile-root` is required at the CLI; the helper returns `None` so `--help` still works without crashing at import.
- **No runtime deps**. Stdlib only. Adding a dependency is a deliberate decision — prefer a stdlib solution first.
