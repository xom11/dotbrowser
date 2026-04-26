# dotbrowser

Manage browser settings as dotfiles. Version-control your browser config and sync it across machines without depending on the browser's own sync service.

> **Status: alpha.** Currently supports **Brave keyboard shortcuts** and **general Brave settings** (Preferences keys without MAC protection — vertical tabs, sidebar toggles, bookmark bar behavior, etc.). The architecture is designed to grow to other browsers (Chromium, Vivaldi, Firefox, ...) and more config domains.

## Why

Chromium-based browsers don't expose most settings through a config file the way Firefox does (`user.js`). To sync custom shortcuts and UI tweaks across machines, your only options have traditionally been:

- the browser's own cloud sync (requires login, opaque storage, only syncs what *it* decides to sync — vertical-tabs collapsed state, sidebar toggles, etc. are local-only)
- clicking through the UI on every machine
- an external extension like Shortkeys (lives outside the browser's native shortcut system)

`dotbrowser` patches the browser's profile JSON directly — for keys that are NOT in the MAC-protected tracked-prefs region — so a single TOML file in your dotfiles repo becomes the source of truth.

## Install

```bash
pip install -e .
```

Or run without installing:

```bash
python -m dotbrowser brave shortcuts list
```

## Usage

### One file, one command

A single `brave.toml` carries both `[shortcuts]` and `[settings]`. One `apply` call writes both in one kill-brave + backup + write cycle:

```toml
# brave.toml
[shortcuts]
toggle_sidebar = ["Control+Shift+KeyE"]
toggle_ai_chat = ["Alt+KeyA"]
focus_location = ["Control+KeyL", "Alt+KeyD"]

# vim-style hjkl: Alt+H/L for history, Alt+J/K for tabs.
back                = ["Alt+KeyH"]
forward             = ["Alt+KeyL"]
select_previous_tab = ["Alt+KeyJ"]
select_next_tab     = ["Alt+KeyK"]

[settings]
"brave.tabs.vertical_tabs_enabled"   = true
"brave.tabs.vertical_tabs_collapsed" = false
"bookmark_bar.show_tab_groups"       = true
```

```bash
# Preview
dotbrowser brave apply brave.toml --dry-run

# Apply (Brave must be closed, or pass --kill-brave)
dotbrowser brave apply brave.toml
dotbrowser brave apply brave.toml --kill-brave   # SIGKILL Brave, apply, restart
```

Either table can be omitted — that module is then skipped (state file untouched). An empty header (`[settings]` with no entries) is the explicit "wipe my managed entries" gesture.

### Inspection

```bash
# What shortcuts am I currently overriding?
dotbrowser brave shortcuts dump

# Find a command by name
dotbrowser brave shortcuts list toggle

# What's the current value of a setting? (useful for building a config)
dotbrowser brave settings dump brave.tabs.vertical_tabs_enabled bookmark_bar.show_tab_groups
```

Shortcut syntax = Chromium [KeyEvent codes](https://www.w3.org/TR/uievents-code/) joined by `+`, e.g. `Control+Shift+KeyP`, `Alt+Digit1`, `F11`.

Settings keys = dotted paths into the profile `Preferences` JSON, e.g. `brave.tabs.vertical_tabs_enabled`, `bookmark_bar.show_tab_groups`.

### Multiple profiles

```bash
dotbrowser brave --profile "Profile 1" apply brave.toml
```

## How it works

Brave keeps user prefs in its profile `Preferences` JSON. `dotbrowser`:

1. Parses the TOML once, hands each table to its module's `plan_apply`. Each module validates and computes its own diff without touching disk.
2. Refuses if Brave is running (Brave overwrites `Preferences` on exit). `--kill-brave` is the escape hatch: capture argv, SIGKILL, apply, relaunch via the OS-correct path (`brave-browser` wrapper on Linux, `open -a "Brave Browser"` on macOS).
3. Backs up `Preferences` once with a timestamp.
4. Applies all module mutations to one in-memory dict, then writes atomically (temp file + rename) — so a failure in one module aborts the whole apply, no partial writes.
5. Tracks managed entries per module in a sidecar file (`Preferences.dotbrowser.shortcuts.json`, `Preferences.dotbrowser.settings.json`). Removing a key from your config resets that shortcut to its default (or pops the setting back to Brave's compiled-in default) on next `apply`.
6. Reloads and verifies the file after writing.

### MAC-protected keys (the v1 limitation)

Some Chromium prefs are "tracked": they have a sibling MAC under `protection.macs.<key>` that Brave verifies on launch. Writing them without updating the MAC silently resets them. The settings module **refuses** any key with a `protection.macs.*` entry in your profile and tells you which keys it rejected — better than a write that vanishes 30 seconds later.

Common refused keys (in this category): `homepage`, `session.startup_urls`, `browser.show_home_button`, `default_search_provider_data.template_url_data`, `pinned_tabs`. MAC support is planned for v2 (requires the Chromium seed + byte-exact serialization).

Default profile root per platform:

| Platform | Path |
|---|---|
| Linux | `~/.config/BraveSoftware/Brave-Browser` |
| macOS | `~/Library/Application Support/BraveSoftware/Brave-Browser` |

## Caveats

- **Linux + macOS** are supported. Windows would need a different `--profile-root` default (`%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data`) and process-management code path.
- **Brave Sync** may overwrite some `[settings]` entries on its next sync pulse if they happen to fall in a synced category. UI-layout keys like `brave.tabs.vertical_tabs_*` are local-only and immune; if you hit a synced one, disable Sync for that category.
- Command-ID mapping is auto-generated. If Brave/Chromium adds new commands you want to bind, regenerate:
  ```bash
  python scripts/generate_brave_command_ids.py
  ```
- Only Brave is supported. Chrome doesn't expose a shortcut customization UI at all (shortcuts are hardcoded), so this approach doesn't apply to vanilla Chrome.

## Roadmap

- [x] macOS profile-root default
- [x] General Brave settings (non-MAC keys)
- [x] Unified `brave apply` for shortcuts + settings in one cycle
- [ ] MAC-protected pref support (homepage, default search engine, ...)
- [ ] Windows profile-root default
- [ ] Other browsers (Vivaldi, Edge, Arc, ...) — same Chromium pref system
- [ ] Firefox via `user.js` generation

## License

MIT
