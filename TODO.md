# TODO

Roadmap for `dotbrowser`, ordered by **value × ease**. Pick from the
top of each tier when you want to grab work. The scopes are rough — refine
when you actually start the task.

If you finish something, move it to the **Done** section at the bottom (with
the commit/PR if relevant) so the trail stays in-repo.

---

## Tier 1 — high value, worth tackling next

### Brave `[pwa]` — macOS implementation

Linux side is done on branch `feat/pwa` (commits `d1b1351`, `bd6f482`,
not yet merged). Force-installs PWAs via Chromium's
`WebAppInstallForceList` policy; verified end-to-end against Brave 147
on Linux ARM64. macOS path is the only blocker before merging to
`main` — `_check_platform_supported()` in `src/dotbrowser/brave/pwa.py`
exits with a clear error on Darwin today.

**Open hypothesis to test first** — does Brave honor
`WebAppInstallForceList` set at user level via
`defaults write com.brave.Browser WebAppInstallForceList -array '...'`,
or only at machine level
(`/Library/Managed Preferences/com.brave.Browser.plist`)?

The policy is declared `scope: machine` in Chromium's policy schema,
which suggests user-level should be ignored. But `CFPreferencesCopyAppValue`
(what Chromium reads policies through) traverses user-level paths too,
so practical behavior may differ. Concrete probe:

```bash
defaults write com.brave.Browser WebAppInstallForceList -array \
  '{"url" = "https://squoosh.app/"; "default_launch_container" = "window"; "create_desktop_shortcut" = 1;}'
open -a "Brave Browser"
# wait ~30s, then check chrome://web-app-internals — does Squoosh appear
# with latest_install_source = "external policy"?
```

- **If user-level works** → no-sudo macOS path: write to
  `~/Library/Preferences/com.brave.Browser.plist` via `plistlib`, skip
  the orchestrator's sudo preflight on Darwin. Strict UX win over Linux.
- **If user-level ignored** → fall back to
  `/Library/Managed Preferences/com.brave.Browser.plist`, write via
  `sudo` + `plistlib`. Same sudo posture as Linux.

**Code locations to touch:**
- `src/dotbrowser/brave/pwa.py` — branch `_check_platform_supported`,
  `POLICY_FILE`, `_read_current_policy`, `_sudo_write_policy` per
  platform via `sys.platform`. plist serialization replaces JSON.
- `src/dotbrowser/brave/__init__.py` — gate the sudo preflight on
  whether the active platform path actually needs it.
- `tests/test_pwa_apply.py` — relax the Linux-only `pytestmark` once
  the macOS branch exists; add Darwin-specific cases if behavior
  diverges (e.g. plist round-trip).
- `examples/brave.toml` + `README.md` — drop the "Linux only" caveat
  in the `[pwa]` block.
- `CLAUDE.md` "Brave pwa: how force-install actually works", point 7 —
  update once macOS lands.

**Architecture decision already recorded:** keep
`WebAppInstallForceList` (real PWA install) over `.desktop` launcher
generation. Reasoning: file handlers, badging API, manifest fidelity,
and Brave-driven manifest auto-update matter for some PWAs;
force-install is the right semantics for declarative "TOML is source
of truth" management. Trade-off accepted: Linux requires `sudo` per
non-empty `[pwa]` apply, and UI uninstall is hidden (by design — the
TOML is the source of truth, not the UI).

### Settings catalog generator

Like `command_ids.py` for shortcuts, but for settings keys. Script
parses `brave-core/components/.../pref_names.h` (and Chromium's
upstream pref name headers) into a generated `pref_names.py` mapping.

**Unlocks:**
- `dotbrowser brave settings list [filter]` — list known pref keys.
- Validate keys at apply time → catch typos early instead of writing
  a key Brave will silently ignore.
- Sort known-MAC vs known-non-MAC at validation time (instead of only
  at apply time when the profile happens to have the entry).

**Scope:** Add `scripts/generate_brave_pref_names.py` (mirrors the
existing `generate_brave_command_ids.py` shape), regenerate, plumb the
mapping into `settings.py` validation. Mid-effort.

### `--list-mac-keys` / `--show-blocked` flag

Print every MAC-protected key in the user's profile so they know
upfront which toggles `apply` will refuse, before they even write a
config. Pairs naturally with the catalog generator: catalog gives the
universe of known keys, this flag tells you which of them your profile
currently has under `protection.macs.*`.

This is the **practical replacement** for full MAC support — instead
of patching MAC-protected prefs ourselves, we tell users clearly which
~5–7 keys (homepage, default search, startup URLs, ...) they need to
set via the Brave UI once and which keys dotbrowser can manage. See
[#1](https://github.com/xom11/dotbrowser/issues/1) for the decision
to skip full MAC v2 support.

---

## Tier 2 — quality-of-life and reach

### Windows support
- New `_default_profile_root()` branch:
  `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data`
- Replace `pgrep`/`/proc/<pid>/cmdline` with `tasklist /v` and WMI
  for cmdline retrieval.
- Replace `pkill` with `taskkill /F /IM brave.exe`.
- Restart via the Start Menu shortcut or registry-detected install
  path.
- `test_platform.py` parametrize for `win32`.

### `dotbrowser brave init`
Scaffold a `brave.toml` with sensible empty `[shortcuts]` and
`[settings]` tables + commented examples. Drops the cold-start
friction of "what do I put in this file?".

### Brave Sync warning
At apply time, detect whether Brave Sync is enabled (look at
`sync.has_setup_completed` or similar) and warn for keys known to be
synced — those changes can be silently overwritten on next sync pulse.

### Settings dump --all
`brave settings dump` currently emits only managed keys. Add `--all`
to dump every non-MAC key under a path prefix so users can browse
what's available.

### CHANGELOG.md
Track user-visible changes per release. `Keep a Changelog` format.

---

## Tier 3 — expand to other browsers

Each is a new module under `src/dotbrowser/<browser>/` that exposes
`register(subparsers)` and (optionally) `plan_apply`. The shared
`utils.py` (write_atomic, kill/restart pattern) covers most plumbing
for Chromium-based browsers.

### Vivaldi
Chromium-based. Profile path: `~/.config/vivaldi`. Different process
name (`vivaldi-bin` on Linux). Has its own command IDs but reuses
Chromium's pref schema.

### Microsoft Edge
Chromium-based. `~/.config/microsoft-edge` or
`~/Library/Application Support/Microsoft Edge`. Has Edge-specific
pref keys (sidebar, collections, vertical tabs in MS's flavor).

### Arc / Chromium / Brave Beta
Mostly path config + verifying pref schema doesn't drift. Low effort
each once the abstraction is right.

### Firefox via user.js
Different stack entirely. Firefox has no JSON `Preferences`; instead
`prefs.js` and `user.js` live in the profile and use
`user_pref("name", value);` syntax. Needs a separate generator.

---

## Done

- [x] Brave shortcuts (non-MAC) — `add brave keyboard shortcut support`
- [x] macOS support for shortcuts
- [x] Brave settings (non-MAC keys) — commit `3046294`
- [x] Unified `brave apply` for shortcuts + settings — commit `29b7465`
- [x] CI on GitHub Actions (Linux + macOS, Python 3.11–3.13)
- [x] Release-to-PyPI workflow on tag push

---

## Deferred — probably won't do

### MAC-protected pref support (v2)

Originally planned as Tier 1 — would unlock writing tracked prefs like
`homepage`, `session.startup_urls`, `browser.show_home_button`,
`default_search_provider_data.template_url_data`, `pinned_tabs`,
`omnibox.prevent_url_elisions`. Decision on 2026-04-26: **skip**.
Tracked in [#1](https://github.com/xom11/dotbrowser/issues/1).

**TL;DR of the decision:**
- Only ~5–7 user-relevant keys are MAC-protected; most overlap with
  things users set once via UI (homepage, default search engine), not
  the rapid-iteration use case dotfile management actually wins at.
- Brave Sync covers cross-machine reproduction for these keys when it
  is on, making dotfile management redundant.
- Implementation cost is high: per-pref MAC + `protection.super_mac`
  + byte-exact JSON serialization matching Chromium's
  `JSONStringValueSerializer`. Re-serializing the entire
  `protection.macs` subtree on every apply is the risky part.
- Maintenance trap: Chromium has changed the MAC algorithm before; a
  silent upstream change would corrupt every user's profile (apply
  succeeds, Brave resets everything to default on next launch).
- Better path: ship the catalog generator + `--list-mac-keys` so users
  know which ~5–7 keys to set via UI, and dotbrowser stays out of the
  crypto business.

Revisit only if multiple users open issues asking for it. Until then,
the refusal in `brave/settings.py::plan_apply` stays as the v1 contract.
