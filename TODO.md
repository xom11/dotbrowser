# TODO

Roadmap for `dotbrowser`, ordered by **value × ease**. Pick from the
top of each tier when you want to grab work. The scopes are rough — refine
when you actually start the task.

If you finish something, move it to the **Done** section at the bottom (with
the commit/PR if relevant) so the trail stays in-repo.

---

## Tier 1 — high value, worth tackling next

### MAC-protected pref support (v2)

Unlock writing to Chromium "tracked prefs": `homepage`,
`session.startup_urls`, `browser.show_home_button`,
`default_search_provider_data.template_url_data`, `pinned_tabs`,
`omnibox.prevent_url_elisions`, etc. Today these are refused at apply
time because writing them without updating their MAC causes Brave to
silently reset them on next launch.

**What it takes:**
- Extract the Chromium `kSeed` constant (public, in
  `services/preferences/tracked/pref_hash_calculator.cc`).
- Implement `derived_key = HMAC-SHA256(seed, profile_path)` and
  `MAC = HMAC-SHA256(derived_key, pref_path + serialized_value)`.
- Match Chromium's `JSONStringValueSerializer` byte-for-byte
  (key order, spacing, escape rules) — the hard part.
- When applying a tracked pref, write the value AND update
  `protection.macs.<dotted_path>` in the same `write_atomic`.
- Add an `--allow-mac` opt-in flag so we don't surprise users on the
  first apply after upgrade.

**Why first:** This is the major remaining feature gap. Users hit the
MAC wall as soon as they try common settings (homepage, default search
engine), and no other tool does this for Brave on Linux.

**Risk:** Medium-high. The crypto is trivial; the JSON serialization
must be exact or every apply breaks. Needs careful end-to-end testing
against a real Brave profile.

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

### `--list-mac-keys` / `--show-blocked` flag
Print every MAC-protected key in the user's profile so they know
upfront which toggles `apply` will refuse, before they even write a
config.

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
