# dotbrowser

[![CI](https://github.com/xom11/dotbrowser/actions/workflows/ci.yml/badge.svg)](https://github.com/xom11/dotbrowser/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dotbrowser.svg)](https://pypi.org/project/dotbrowser/)
[![Python](https://img.shields.io/pypi/pyversions/dotbrowser.svg)](https://pypi.org/project/dotbrowser/)

Manage your browser as a dotfile. Keep shortcuts and UI tweaks in a single TOML, apply with one command, sync across machines — no browser cloud sync required.

> **Status: alpha.** Brave + Vivaldi + Edge on Linux, macOS, and Windows — keyboard shortcuts (Brave / Vivaldi), general settings (vertical tabs, sidebar, NTP & toolbar declutter, …), and force-installed PWAs. Edge supports `[settings]` and `[pwa]` only — Microsoft Edge does not expose customizable accelerators in `Preferences`. A shared `_base/` module makes adding more Chromium-based browsers a ~150-line job.

## Quick start

The repo ships opinionated examples under [`examples/brave/`](examples/brave/), [`examples/vivaldi/`](examples/vivaldi/), and [`examples/edge/`](examples/edge/): vertical tabs collapsed to icons, decluttered new tab page, stripped-down toolbar, vim-style hjkl shortcuts. Each `all.toml` bundles all available namespaces; `shortcuts.toml`, `settings.toml`, and `pwa.toml` are single-namespace variants.

![Brave with the minimal config — empty new tab page, vertical tabs collapsed to icons, decluttered toolbar](docs/img/minimal-brave.png)

**Scaffold a starter config from scratch:**

```bash
dotbrowser brave init                        # write commented template to stdout
dotbrowser brave init -o brave.toml          # ...or to a file
dotbrowser vivaldi init -o vivaldi.toml
dotbrowser edge init -o edge.toml
```

**Or apply an example directly from GitHub** — no clone, no install. Fetched payloads are echoed with byte size + SHA-256 so you can see exactly what's being applied:

```bash
uvx dotbrowser brave apply --dry-run \
  https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave/all.toml

# Apply (force-kills Brave + restarts)
uvx dotbrowser brave apply -k \
  https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave/all.toml
```

Prefer to inspect / customise locally first? Download then apply:

```bash
curl -fsSL -o brave.toml https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave/all.toml
# edit brave.toml ...
uvx dotbrowser brave apply brave.toml -k
```

The Vivaldi (`vivaldi/all.toml`) and Edge (`edge/all.toml`) variants follow the same shape. Anything you later remove from your config reverts to the browser's default on the next `apply` — no orphan entries.

## Install

```bash
pipx install dotbrowser     # global, isolated venv
uvx dotbrowser <args>       # run on demand, no install step
pip install dotbrowser      # into the active environment
```

Pin a version: `pipx install "dotbrowser>=0.3,<0.4"`. Run a specific tag: `uvx --from 'dotbrowser==0.3.0' dotbrowser <args>`. Run from a branch: `uvx --from git+https://github.com/xom11/dotbrowser dotbrowser <args>`. Local dev: `pip install -e ".[test]"`.

## Build your own config

A single TOML carries `[shortcuts]`, `[settings]` and `[pwa]`. One `apply` writes all three in a single backup + write cycle.

```toml
# brave.toml (Vivaldi has the same shape; Edge skips [shortcuts])
[shortcuts]
toggle_sidebar = ["Control+Shift+KeyE"]
toggle_ai_chat = ["Alt+KeyA"]

# vim-style hjkl
back                = ["Alt+KeyH"]
forward             = ["Alt+KeyL"]
select_previous_tab = ["Alt+KeyJ"]
select_next_tab     = ["Alt+KeyK"]

# Same chord on all OSes — Meta+ = Cmd on macOS, Super on Linux/Windows (auto-translated)
new_tab   = ["Control+KeyT", "Meta+KeyT"]
close_tab = ["Control+KeyW", "Meta+KeyW"]

[settings]
"brave.tabs.vertical_tabs_enabled"   = true
"brave.tabs.vertical_tabs_collapsed" = true
"bookmark_bar.show_on_all_tabs"      = false

[pwa]
# Force-installed Progressive Web Apps. The browser fetches each manifest,
# downloads icons, registers the app in chrome://apps, and emits a
# launcher (.desktop on Linux, app shim on macOS, Start Menu shortcut on
# Windows). Removing a URL + re-applying = uninstall.
urls = [
  "https://squoosh.app/",
  "https://app.element.io/",
]
```

```bash
dotbrowser brave   apply brave.toml --dry-run    # preview the diff
dotbrowser brave   apply brave.toml -k           # apply, force-kill + restart Brave
dotbrowser vivaldi apply vivaldi.toml -k         # same flags, different browser
dotbrowser edge    apply edge.toml -k            # Edge: settings + pwa only
```

- **Shortcut keys (Brave)**: Chromium [KeyEvent codes](https://www.w3.org/TR/uievents-code/) joined by `+` — `Control+Shift+KeyP`, `Alt+Digit1`, `F11`. `Meta+` is auto-translated to `Command+` on macOS.
- **Shortcut keys (Vivaldi)**: lowercase, tokenized with `+` — `meta+t`, `ctrl+shift+e`. Vivaldi uses the same `meta+` spelling on every platform (cmd on macOS, win/super on Linux/Windows), so no platform translation is needed.
- **Edge has no `[shortcuts]`**: Microsoft Edge hardcodes accelerators and exposes no `Preferences` key for them. Use `[settings]` and `[pwa]` instead.
- **Setting keys**: dotted paths into the profile `Preferences` JSON. MAC-protected keys (`homepage`, default search engine, `pinned_tabs`, …) are refused with a clear error.
- **PWA URLs**: every entry installs with `default_launch_container = "window"` (standalone PWA window) and `create_desktop_shortcut = true`. `[pwa]` is the only namespace that needs elevated privileges: it writes a managed-policy file (sudo on Linux/macOS) or the Windows Registry (administrator). If your config has no `[pwa]` table or no diff to apply, no elevation prompt happens.
- **Empty `[settings]` header** (no entries) wipes everything dotbrowser previously managed in that namespace. **Missing header** = skip the namespace entirely. Same rule applies to `[shortcuts]` and `[pwa]`.

## CLI reference

Shape: `dotbrowser <browser> [browser-flags] <action> [action-flags] [args]`. The same actions work for `brave`, `vivaldi`, and `edge` (Edge omits the `shortcuts` action).

### Browser-level flags

These apply to **every** action under a browser and go *before* the action name.

| Flag | Default | What it does |
|---|---|---|
| `-r, --profile-root PATH` | see table below | Browser's root profile directory. Auto-detected on Linux / macOS / Windows. |
| `-p, --profile NAME` | `Default` | Profile directory name inside the root — e.g. `"Profile 1"`, `Default`. |
| `--channel {stable,beta,nightly}` | `stable` | **Brave only.** Selects the release channel; auto-detects the corresponding `Brave-Browser-Beta` / `Brave-Browser-Nightly` profile path. |

| Browser | Linux | macOS | Windows |
|---|---|---|---|
| `brave`   | `~/.config/BraveSoftware/Brave-Browser` | `~/Library/Application Support/BraveSoftware/Brave-Browser` | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` |
| `vivaldi` | `~/.config/vivaldi` | `~/Library/Application Support/Vivaldi` | `%LOCALAPPDATA%\Vivaldi\User Data` |
| `edge`    | `~/.config/microsoft-edge` | `~/Library/Application Support/Microsoft Edge` | `%LOCALAPPDATA%\Microsoft\Edge\User Data` |

For Brave Beta / Nightly the same paths apply with a `-Beta` / `-Nightly` suffix on the `Brave-Browser` directory (Snap and Flatpak only ship stable, so non-stable channels skip those probes).

```bash
# Apply on a non-default profile, with an alternate root.
dotbrowser brave -r /custom/path -p "Profile 1" apply brave.toml

# Apply against Brave Beta (auto-detects ~/.config/BraveSoftware/Brave-Browser-Beta).
dotbrowser brave --channel beta apply brave.toml
```

### `init` — scaffold a starter TOML

Writes a commented template with example shortcuts (Brave / Vivaldi only), settings, and a commented-out `[pwa]` block. Refuses to overwrite an existing file.

| Flag | What it does |
|---|---|
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave   init -o brave.toml
dotbrowser vivaldi init -o vivaldi.toml
dotbrowser edge    init -o edge.toml
```

### `apply <config>` — write `[shortcuts]` + `[settings]` + `[pwa]`

`<config>` is a local TOML file path **or** an `http://`/`https://` URL. URLs are fetched in-memory; the URL, byte size, and SHA-256 are printed before the diff so you can verify exactly what's about to be applied. If the config contains a non-empty `[pwa]` table, dotbrowser runs a privilege preflight (sudo on Linux/macOS, admin check on Windows) *before* killing the browser, so an auth failure cannot strand the user with a dead browser.

| Flag | What it does |
|---|---|
| `-n, --dry-run` | Compute + print the diff. Do not back up, write, or touch state files. |
| `-k, --kill-browser` | If the browser is running, force-kill it, apply, then restart via the OS-correct launcher (Linux wrapper script, `open -a "<App Name>"` on macOS, the standard `.exe` path on Windows). Without this flag, dotbrowser refuses to run while the browser is open. |

```bash
dotbrowser brave   apply brave.toml --dry-run
dotbrowser brave   apply brave.toml -k
dotbrowser vivaldi apply vivaldi.toml -k
dotbrowser edge    apply edge.toml -k
dotbrowser brave   apply -k https://raw.githubusercontent.com/xom11/dotbrowser/main/examples/brave/all.toml
```

### `shortcuts dump` — emit current shortcuts as TOML (Brave + Vivaldi)

By default, only user-customised bindings are emitted (a useful starting point for your own config).

| Flag | What it does |
|---|---|
| `-a, --all` | Dump every binding, including the browser's compiled-in defaults. |
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave   shortcuts dump
dotbrowser vivaldi shortcuts dump -a -o all-binds.toml
```

### `shortcuts list [filter]` — search known command names (Brave + Vivaldi)

Lists every command id you can bind to. The optional positional `filter` is a substring match.

```bash
dotbrowser brave   shortcuts list toggle    # everything containing "toggle"
dotbrowser vivaldi shortcuts list           # full list
```

### `settings dump [keys ...]` — inspect setting values

- **No keys** → dumps every setting dotbrowser is currently managing on this profile.
- **Explicit keys** → dumps those dotted paths. Missing keys appear as commented-out lines so you know dotbrowser looked.

| Flag | What it does |
|---|---|
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave settings dump
dotbrowser edge  settings dump browser.show_home_button bookmark_bar.show_on_all_tabs
```

### `settings blocked` — list MAC-protected keys `apply` will refuse

Walks `protection.macs` in your profile and prints every tracked pref path as commented TOML, with the current value when present. Use this to learn upfront which keys (e.g. `homepage`, `default_search_provider_data.template_url_data`, `session.startup_urls`) you need to set via the browser UI instead of the config.

| Flag | What it does |
|---|---|
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave   settings blocked
dotbrowser vivaldi settings blocked
dotbrowser edge    settings blocked
```

### `pwa dump` — emit currently-managed PWA URLs as TOML

Reads the managed-policy source for that browser (Linux: `/etc/<browser>/policies/managed/dotbrowser-pwa.json`; macOS: `/Library/Managed Preferences/<bundle-id>.plist`; Windows: `HKLM\Software\Policies\<vendor>\<browser>\WebAppInstallForceList`) and prints a `[pwa]` table you can paste straight into your config. All sources are readable without elevation.

| Flag | What it does |
|---|---|
| `-o, --output FILE` | Write to FILE instead of stdout. |

```bash
dotbrowser brave   pwa dump
dotbrowser vivaldi pwa dump
dotbrowser edge    pwa dump
```

## How it works

`dotbrowser` patches the profile `Preferences` JSON directly. It refuses to run while the browser is open (the browser overwrites the file on exit) — `-k` is the escape hatch: force-kill, apply, restart. Each apply takes one timestamped backup, writes atomically (temp file + rename), and verifies the result by reloading.

`[shortcuts]` and `[settings]` track managed entries per namespace in sidecar files (`Preferences.dotbrowser.{shortcuts,settings}.json`), so removing a key from your config restores the browser's default on the next `apply`. `[pwa]` is different: its state lives in Chromium's managed-policy storage (Linux JSON file, macOS plist, Windows Registry) — the policy *is* the state, no sidecar. The browser reads the policy at startup, fetches each URL's manifest, downloads icons, and emits a launcher (`.desktop` file on Linux, app shim on macOS, Start Menu shortcut on Windows); removing a URL from `[pwa]` and re-applying triggers an uninstall on next launch — same TOML-is-source-of-truth round-trip as the other namespaces.

### Architecture

All Chromium-based browsers share `src/dotbrowser/_base/`: orchestrator, settings logic (with MAC-protected refusal), PWA logic (sudo / registry write paths), and the platform-aware `BrowserProcess` class. Each browser module (`brave/`, `vivaldi/`, `edge/`) is a thin wrapper that configures `_base/` with its profile path, process names, policy file location, and any browser-specific quirks (Brave's numeric command IDs and `Meta+`↔`Command+` rewrite; Vivaldi's `vivaldi.actions[0]` storage shape). Adding a new Chromium browser is roughly 150 lines.

### Brave install methods

| Install | Auto-detected | `-k` works | `[pwa]` works | Notes |
|---|---|---|---|---|
| `.deb` (Debian / Ubuntu apt repo) | yes | yes | yes | Reference install; full support. |
| `.rpm` (Fedora / RHEL dnf repo) | yes | yes | yes | Same paths as `.deb` (Chromium upstream convention). |
| Arch / `pacman` | yes | yes | yes | Same. |
| NixOS (`pkgs.brave`) | yes | yes | yes | Same. |
| **Snap** (`sudo snap install brave`) | yes (probes `~/snap/brave/current/.config/...`) | yes | **refused with clear error** — sandbox doesn't read `/etc/brave/policies/managed/` | Use `.deb` for `[pwa]` support. |
| **Flatpak** (`flathub com.brave.Browser`) | yes (probes `~/.var/app/com.brave.Browser/config/...`) | yes — the inner brave binary is still named `brave` so `pgrep -x` matches; restart goes back through `flatpak run com.brave.Browser` | **refused with clear error** — same sandbox limitation as Snap | Use `.deb` for `[pwa]` support. |
| **macOS** `.dmg` | yes | yes | yes | Includes the cfprefsd cache invalidation needed for `[pwa]`. |
| **Windows** installer | yes (auto-detects `%LOCALAPPDATA%`) | yes — uses `taskkill` / `tasklist`; restarts via the standard `brave.exe` path | yes — writes to Windows Registry (`HKLM\...\WebAppInstallForceList`); requires running as Administrator | |

Dual-install machines (e.g. `.deb` + Snap installed side-by-side) prefer the direct-install profile, matching what `which brave-browser` resolves to.

## Caveats

- **Cloud sync** (Brave Sync, Edge Sync, Vivaldi Sync) can overwrite `[settings]` entries on its next pulse if they fall in a synced category. UI-layout keys like `brave.tabs.vertical_tabs_*` are local-only and immune.
- **Brave / Vivaldi / Edge — not Chrome.** Chrome hardcodes keyboard shortcuts in C++ (there is no `Preferences` key to override), and most useful settings (`homepage`, `session.startup_urls`, `browser.show_home_button`, `pinned_tabs`, …) are MAC-protected in `Secure Preferences` — Chrome computes an HMAC per value and silently resets any externally-modified entry on launch. Brave and Vivaldi expose dedicated, non-MAC-protected prefs (`brave.accelerators`, `vivaldi.actions`) specifically for user customisation. Edge inherits Chromium's MAC protection on most prefs but has enough non-protected keys to make `[settings]` useful (and `[pwa]` works via the same enterprise-policy mechanism as Brave). Edge has no customisable accelerators — that's why `[shortcuts]` is unsupported there.
- A handful of settings (`homepage`, default search engine, `pinned_tabs`, …) are integrity-protected and can't be patched yet — dotbrowser refuses them with a clear error rather than letting the change silently disappear on next launch. Set those via the browser UI for now.
- **`[pwa]` is force-install** (Chromium's enterprise `WebAppInstallForceList`). Apps installed this way appear in `chrome://apps` (or `edge://apps`) with an "Installed by your administrator" label and the right-click "Remove" option is hidden — to uninstall, delete the URL from `[pwa]` and re-apply, then dotbrowser does the rest. This is the right semantics for dotfile-style management (the TOML is the source of truth) but worth knowing if you also install PWAs by hand via the address-bar Install button.

## Roadmap

Open work is tracked on the [issues page](https://github.com/xom11/dotbrowser/issues): settings catalog generator, Brave / Edge Beta / Nightly auto-detection, macOS live CI, more Chromium browsers, and more.

## License

MIT
