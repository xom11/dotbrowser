# Roadmap

Loose ideas not yet ready to be GitHub issues. Promote to an issue once
the design is concrete enough to act on. Tracked bugs and tractable
items live in [issues](https://github.com/xom11/dotbrowser/issues).

## Atomicity & robustness

- ~~**Partial-failure recovery for `[pwa]` writes.**~~ ✅ Done — the
  orchestrator now runs `external_apply_fn` *before* `write_atomic`,
  so a sudo / I/O failure leaves Preferences untouched. Trade-off
  accepted: a flaky sudo means the whole apply (prefs + shortcuts)
  rolls back, which is preferred over silent drift between prefs and
  the policy file. Pinned by
  `tests/test_unified_apply.py::test_external_failure_leaves_prefs_unchanged`.

- **Concurrent-apply lock.** Two `apply` runs in parallel can race on
  `Preferences` and on the state sidecars. Add `fcntl.flock` (or a
  `.lock` file with PID + mtime) on the profile dir.

- **macOS plist read-modify-write race.** `_base/pwa.py:build_policy_payload`
  on macOS reads `/Library/Managed Preferences/...plist`, replaces just
  the `WebAppInstallForceList` key, and writes back. If an MDM updates
  the same plist between read and write, the MDM change is lost. Window
  is small but real; consider a compare-and-swap based on pre-read mtime.

## Refactor & cleanup

- **Single source of truth for `version`.** `pyproject.toml` and
  `src/dotbrowser/__init__.py` both hold the version literal. Hatchling
  supports `[tool.hatch.version] path = "src/dotbrowser/__init__.py"` —
  switch and remove the `version =` line from pyproject.

- **Factor the per-browser `pwa.py` boilerplate.** `brave/pwa.py`,
  `vivaldi/pwa.py`, and `edge/pwa.py` are ~70 lines each of nearly
  identical thin wrappers around `_base.pwa`. The pattern exists so
  tests can monkeypatch `POLICY_FILE` and `_sudo_write_policy` per
  browser. A factory `make_pwa_module(cfg) -> ModuleType` could emit
  the same shape with less duplication while staying patchable.

- **`edge/settings.py` uses `lambda` for `plan_apply`** while Brave and
  Vivaldi use `def`. Tracebacks from a `<lambda>` are slightly less
  readable. Switch for consistency.

- **`_pop_value` leaves empty parent dicts behind.** Cosmetic, but
  makes `dump --all` show empty `{}` nodes. Walk up and prune empty
  ancestors after pop.

- **`Plan.empty` should skip state-sidecar rewrite when truly no-op.**
  Currently a re-apply of an identical config still rewrites the state
  file (mtime bumps). Skip when both `target_keys` and `removed_keys`
  are unchanged.

## Feature ideas

- **`<browser> restore [--from <backup>]`.** Backups already land at
  `Preferences.bak.<timestamp>`. A `restore` subcommand picks the most
  recent (or a named one) and copies it back. Saves users from `cp`
  with the wrong path on macOS.

- **Dedicated `diff` subcommand.** Today the only preview is
  `apply --dry-run`, which reuses apply's output format. A real `diff`
  with exit code 1 on differences is what `pre-commit` hooks and CI
  want.

- **Per-entry options in `[pwa]`.** v1 hardcodes
  `default_launch_container = "window"` and
  `create_desktop_shortcut = true`. Allow either the current
  `urls = ["..."]` form or
  `urls = [{ url = "...", launch_container = "tab" }]`.

- **Vivaldi sync detection.** `_sync_enabled` reads
  `sync.has_setup_completed`, the Chromium key. Vivaldi has its own
  `vivaldi_sync.*` subtree. Detect it and warn analogously to the Brave
  Sync warning.

- **`settings dump --diff <config>`.** Emit only the keys whose current
  value differs from the proposed value. Fastest way to debug "I
  applied this but it doesn't look right".
