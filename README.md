# dotbrowser

[![CI](https://github.com/xom11/dotbrowser/actions/workflows/ci.yml/badge.svg)](https://github.com/xom11/dotbrowser/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dotbrowser.svg)](https://pypi.org/project/dotbrowser/)
[![Python](https://img.shields.io/pypi/pyversions/dotbrowser.svg)](https://pypi.org/project/dotbrowser/)

Manage your browser as a dotfile. Keep Brave shortcuts and UI tweaks in a single TOML, apply with one command, sync across machines — no browser cloud sync required.

> **Status: alpha.** Brave on Linux + macOS — keyboard shortcuts and general settings (vertical tabs, sidebar, NTP & toolbar declutter, …). Architecture is designed to grow to other browsers.

## Quick start

The repo ships an opinionated [`examples/brave.toml`](examples/brave.toml): vertical tabs collapsed to icons, decluttered new tab page, stripped-down toolbar, vim-style hjkl shortcuts.

![Brave with the minimal config — empty new tab page, vertical tabs collapsed to icons, decluttered toolbar](docs/img/minimal-brave.png)

No clone, no install — `apply` accepts a URL directly. Fetched payloads are echoed with byte size + SHA-256 so you can see exactly what's being applied:

```bash
uvx dotbrowser brave apply --dry-run \
  https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave.toml

# Apply (SIGKILLs Brave + restarts)
uvx dotbrowser brave apply -k \
  https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave.toml
```

Prefer to inspect / customise locally first? Download then apply:

```bash
curl -fsSL -o brave.toml https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave.toml
# edit brave.toml ...
uvx dotbrowser brave apply brave.toml -k
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

## CLI reference

Shape: `dotbrowser <browser> [browser-flags] <action> [action-flags] [args]`.

### Browser-level flags (`dotbrowser brave …`)

These apply to **every** action under `brave` and go *before* the action name.

| Flag | Default | What it does |
|---|---|---|
| `-r, --profile-root PATH` | Linux: `~/.config/BraveSoftware/Brave-Browser` <br> macOS: `~/Library/Application Support/BraveSoftware/Brave-Browser` | Brave's root profile directory. Required on Windows / unsupported platforms. |
| `-p, --profile NAME` | `Default` | Profile directory name inside the root — e.g. `"Profile 1"`, `Default`. |

```bash
# Apply on a non-default profile, with an alternate root.
dotbrowser brave -r /custom/path -p "Profile 1" apply brave.toml
```

### `apply <config>` — write `[shortcuts]` + `[settings]`

`<config>` is a local TOML file path **or** an `http://`/`https://` URL. URLs are fetched in-memory; the URL, byte size, and SHA-256 are printed before the diff so you can verify exactly what's about to be applied.

| Flag | What it does |
|---|---|
| `-n, --dry-run` | Compute + print the diff. Do not back up, write, or touch state files. |
| `-k, --kill-browser` | If Brave is running, `SIGKILL` it, apply, then restart via the OS-correct launcher (`brave-browser` wrapper on Linux, `open -a "Brave Browser"` on macOS). Without this flag, dotbrowser refuses to run while Brave is open. |

```bash
dotbrowser brave apply brave.toml --dry-run
dotbrowser brave apply brave.toml -k
dotbrowser brave apply -k https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave.toml
```

### `shortcuts dump` — emit current shortcuts as TOML

By default, only user-customised bindings are emitted (a useful starting point for your own config).

| Flag | What it does |
|---|---|
| `-a, --all` | Dump every binding, including Brave's compiled-in defaults. |
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave shortcuts dump                       # what am I overriding?
dotbrowser brave shortcuts dump -a -o all-binds.toml  # full reference dump
```

### `shortcuts list [filter]` — search known command names

Lists every command id you can bind to. The optional positional `filter` is a substring match.

```bash
dotbrowser brave shortcuts list toggle   # everything containing "toggle"
dotbrowser brave shortcuts list          # full list
```

### `settings dump [keys ...]` — inspect setting values

- **No keys** → dumps every setting dotbrowser is currently managing on this profile.
- **Explicit keys** → dumps those dotted paths. Missing keys appear as commented-out lines so you know dotbrowser looked.

| Flag | What it does |
|---|---|
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave settings dump
dotbrowser brave settings dump brave.tabs.vertical_tabs_enabled bookmark_bar.show_on_all_tabs
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
