# Live Apply Without Restart Design

## Goal

Allow Brave and Vivaldi settings and shortcuts to apply while the browser process keeps running, without `--kill-browser`.

## Findings

Direct edits to `Preferences` are not live. When Brave or Vivaldi is running, each browser keeps preferences in memory; if the file is edited underneath it, the runtime state does not change and a normal browser shutdown rewrites the file from memory.

Both browsers do have privileged internal APIs that can update the running preference service:

- Brave settings use `chrome.settingsPrivate.setPref`.
- Brave shortcuts use the Settings shortcuts page `CommandsService` Mojo client exposed by `/commands.bundle.js`.
- Vivaldi settings and shortcuts use `vivaldi.prefs.set`.
- Vivaldi shortcuts need a reload of the internal Vivaldi UI page after `vivaldi.actions` changes; this reloads the UI extension, not the browser process.

PWA policy is external to `Preferences`. Chromium marks `WebAppInstallForceList` as dynamic-refresh capable, but Windows policy writes still require Administrator.

## User-Facing Shape

Make `dotbrowser <browser> apply CONFIG` the normal live path.
Keep `launch --live-port PORT [url]` and `apply --live-port PORT` as
advanced/debug controls.

Plain `apply` means:

- If the browser is not running, use the existing offline file path.
- If the browser is running with an existing DevTools endpoint, apply through browser APIs.
- If the browser is running without an endpoint, request a normal close, relaunch once with `--remote-debugging-address=127.0.0.1`, then apply through browser APIs.
- Store the discovered/launched endpoint in `.dotbrowser.live.json` under the profile root so later `apply` runs do not need `launch` or `--live-port`.
- `--kill-browser` still forces the old force-kill offline path.
- `--live-port` is advanced/debug override, not a normal user requirement.

The endpoint is always bound to `127.0.0.1`; no wide remote-debugging port is opened.

## Data Flow

The existing planner remains the source of validation and diff output. The live path builds the same plans, then applies them to an in-memory copy of `Preferences` to compute the target state. Browser-specific live adapters translate that target state into internal browser API calls.

For live apply:

1. Load TOML and existing `Preferences`.
2. Build normal `Plan` objects and print the normal diff.
3. Run PWA external writes first when needed.
4. Compute target prefs by running each plan's `apply_fn` on a copy.
5. Send only changed settings/shortcut values through the browser API.
6. Write dotbrowser sidecar state files.
7. Verify through browser API where practical.

## Scope

Implemented now:

- Brave live settings and shortcuts.
- Vivaldi live settings and shortcuts.
- PWA policy write while the browser is running when elevation is available.
- Plain `apply` auto-discovers an existing endpoint or normal-close/relaunches once to create one.
- Sidecar endpoint memory for later plain `apply` runs.

Not implemented now:

- A long-lived secure agent.
- Edge/Chrome live apply; they do not have shortcuts in this project and can keep using offline apply for settings.

## Tests

Unit tests should cover:

- plain `apply` on a running browser auto-runs the live path and does not write `Preferences`.
- `--live-port` bypasses auto-discovery and uses the requested port.
- `--live-port` conflicts with `--kill-browser`.
- auto-relaunch remembers the endpoint sidecar.
- Live apply writes sidecar state files.
- Brave live adapter uses `settingsPrivate` for settings and `commandsCache` for shortcuts.
- Vivaldi live adapter uses `vivaldi.prefs.set` and reloads UI after shortcut changes.
