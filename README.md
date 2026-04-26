# dotbrowser

Manage browser settings as dotfiles. Version-control your browser config and sync it across machines without depending on the browser's own sync service.

> **Status: alpha.** Currently supports **Brave keyboard shortcuts**. The architecture is designed to grow to other browsers (Chromium, Vivaldi, Firefox, ...) and other config domains (search engines, flags, theme, ...).

## Why

Chromium-based browsers don't expose most settings through a config file the way Firefox does (`user.js`). To sync a custom keymap across machines, your only options have traditionally been:

- the browser's own cloud sync (requires login, opaque storage)
- clicking through the UI on every machine
- an external extension like Shortkeys (lives outside the browser's native shortcut system)

`dotbrowser` patches the browser's profile JSON directly — for keys that are NOT in the protected/MAC-checked region — so a TOML file in your dotfiles repo becomes the source of truth.

## Install

```bash
pip install -e .
```

Or run without installing:

```bash
python -m dotbrowser brave shortcuts list
```

## Usage

### Brave keyboard shortcuts

```bash
# What's currently customized?
dotbrowser brave shortcuts dump

# Find a command by name
dotbrowser brave shortcuts list toggle

# Preview changes from a config file
dotbrowser brave shortcuts apply examples/brave.shortcuts.toml --dry-run

# Apply (Brave must be closed)
dotbrowser brave shortcuts apply examples/brave.shortcuts.toml
```

Example config — only list what you want to override; everything else stays at Brave's default:

```toml
[shortcuts]
toggle_sidebar = ["Control+Shift+KeyE"]
toggle_ai_chat = ["Alt+KeyA"]
focus_location = ["Control+KeyL", "Alt+KeyD"]
```

Shortcut syntax = Chromium [KeyEvent codes](https://www.w3.org/TR/uievents-code/) joined by `+`, e.g. `Control+Shift+KeyP`, `Alt+Digit1`, `F11`.

### Multiple profiles

```bash
dotbrowser brave --profile "Profile 1" shortcuts apply config.toml
```

## How it works

Brave stores accelerators in its profile `Preferences` JSON under the key `brave.accelerators`. This is **regular** preferences (not `Secure Preferences`), so it has no MAC integrity check — direct editing works.

Default profile root per platform:

| Platform | Path |
|---|---|
| Linux | `~/.config/BraveSoftware/Brave-Browser` |
| macOS | `~/Library/Application Support/BraveSoftware/Brave-Browser` |

`dotbrowser`:

1. Reads the TOML config and resolves human-friendly names → Chromium command IDs (sourced from `chrome/app/chrome_command_ids.h` + `brave/app/brave_command_ids.h`).
2. Refuses to write while Brave is running (Brave overwrites Preferences on exit).
3. Backs up `Preferences` with a timestamp.
4. Patches the JSON atomically (temp file + rename).
5. Tracks which command IDs it manages in a sidecar file (`Preferences.dotbrowser.shortcuts.json`) so removing a key from your config on the next `apply` resets that shortcut to its default.
6. Verifies by reloading the file after writing.

## Caveats

- **Linux + macOS** are supported. Windows would need a different `--profile-root` default (`%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data`) and process-management code path.
- Command-ID mapping is auto-generated. If Brave/Chromium adds new commands you want to bind, regenerate:
  ```bash
  python scripts/generate_brave_command_ids.py
  ```
- Only Brave is supported. Chrome doesn't expose a shortcut customization UI at all (shortcuts are hardcoded), so this approach doesn't apply to vanilla Chrome.

## Roadmap

- [ ] More Brave config domains (flags, search engines, theme, startup tabs)
- [x] macOS profile-root default
- [ ] Windows profile-root default
- [ ] Other browsers (Vivaldi, Edge, Arc, ...) — same Chromium pref system
- [ ] Firefox via `user.js` generation

## License

MIT
