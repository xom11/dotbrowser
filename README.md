# dotbrowser

[![CI](https://github.com/xom11/dotbrowser/actions/workflows/ci.yml/badge.svg)](https://github.com/xom11/dotbrowser/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dotbrowser.svg)](https://pypi.org/project/dotbrowser/)
[![Python](https://img.shields.io/pypi/pyversions/dotbrowser.svg)](https://pypi.org/project/dotbrowser/)

Manage your browser as a dotfile. Keep Brave shortcuts and UI tweaks in a single TOML, apply with one command, sync across machines — no browser cloud sync required.

> **Status: alpha.** Brave on Linux + macOS — keyboard shortcuts and general settings (vertical tabs, sidebar, NTP & toolbar declutter, …). Architecture is designed to grow to other browsers.

## Quick start

The repo ships an opinionated [`examples/brave.toml`](examples/brave.toml): vertical tabs collapsed to icons, decluttered new tab page, stripped-down toolbar, vim-style hjkl shortcuts.

![Brave with the minimal config — empty new tab page, vertical tabs collapsed to icons, decluttered toolbar](docs/img/minimal-brave.png)

No clone, no install — fetch the config straight from GitHub and apply with `uvx`:

```bash
curl -fsSL -o brave.toml https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave.toml
uvx dotbrowser brave apply brave.toml --dry-run    # preview the diff
uvx dotbrowser brave apply brave.toml -k           # apply — SIGKILLs Brave + restarts
```

Anything you later remove from `brave.toml` reverts to Brave's default on the next `apply` — no orphan entries.

## Install

```bash
pipx install dotbrowser     # global, isolated venv
uvx dotbrowser <args>       # run on demand, no install step
pip install dotbrowser      # into the active environment
```

Pin a version: `pipx install "dotbrowser>=0.3,<0.4"`. Run a specific tag: `uvx --from 'dotbrowser==0.3.0' dotbrowser <args>`. Run from a branch: `uvx --from git+https://github.com/xom11/dotbrowser dotbrowser <args>`. Local dev: `pip install -e ".[test]"`.

## Build your own config

`brave.toml` carries `[shortcuts]` and `[settings]`. One `apply` writes both in a single backup + write cycle.

```toml
# brave.toml
[shortcuts]
toggle_sidebar = ["Control+Shift+KeyE"]
toggle_ai_chat = ["Alt+KeyA"]

# vim-style hjkl
back                = ["Alt+KeyH"]
forward             = ["Alt+KeyL"]
select_previous_tab = ["Alt+KeyJ"]
select_next_tab     = ["Alt+KeyK"]

# Same chord on both OSes — Meta+ = Cmd on macOS, Super on Linux (auto-translated)
new_tab   = ["Control+KeyT", "Meta+KeyT"]
close_tab = ["Control+KeyW", "Meta+KeyW"]

[settings]
"brave.tabs.vertical_tabs_enabled"   = true
"brave.tabs.vertical_tabs_collapsed" = true
"bookmark_bar.show_on_all_tabs"      = false
```

```bash
dotbrowser brave apply brave.toml --dry-run    # preview the diff
dotbrowser brave apply brave.toml -k           # apply, SIGKILL + restart Brave
```

- **Shortcut keys**: Chromium [KeyEvent codes](https://www.w3.org/TR/uievents-code/) joined by `+` — `Control+Shift+KeyP`, `Alt+Digit1`, `F11`.
- **Setting keys**: dotted paths into the profile `Preferences` JSON.
- **Empty `[settings]` header** (no entries) wipes everything dotbrowser previously managed in that namespace. **Missing header** = skip the namespace entirely.

### Inspect

```bash
dotbrowser brave shortcuts dump                                  # what am I currently overriding?
dotbrowser brave shortcuts list toggle                           # search command names
dotbrowser brave settings dump brave.tabs.vertical_tabs_enabled  # current value(s) of a setting
```

### Multiple profiles

```bash
dotbrowser brave -p "Profile 1" apply brave.toml
```

## How it works

`dotbrowser` patches Brave's profile `Preferences` JSON directly. It refuses to run while Brave is open (Brave overwrites the file on exit) — `-k` is the escape hatch: SIGKILL Brave, apply, restart. Each apply takes one timestamped backup, writes atomically (temp file + rename), and verifies the result by reloading.

Managed entries are tracked per namespace in sidecar files (`Preferences.dotbrowser.{shortcuts,settings}.json`), so removing a key from your config restores Brave's default on the next `apply`.

Default profile root: `~/.config/BraveSoftware/Brave-Browser` on Linux, `~/Library/Application Support/BraveSoftware/Brave-Browser` on macOS. Override with `-r/--profile-root`.

## Caveats

- **Brave Sync** can overwrite `[settings]` entries on its next pulse if they fall in a synced category. UI-layout keys like `brave.tabs.vertical_tabs_*` are local-only and immune.
- **Linux + macOS only.** Windows needs a custom `--profile-root` and process-management path.
- **Brave only.** Chrome hardcodes shortcuts (no UI to customize), so this approach doesn't apply.
- A handful of settings (`homepage`, default search engine, `pinned_tabs`, …) are integrity-protected and can't be patched yet — dotbrowser refuses them with a clear error rather than letting the change silently disappear on next launch. Set those via the Brave UI for now.

## Roadmap

Open items live in [TODO.md](TODO.md): expanded settings coverage (homepage / default search / pinned tabs), settings catalog generator, Windows support, more browsers (Vivaldi, Edge, Arc, Firefox).

## License

MIT
