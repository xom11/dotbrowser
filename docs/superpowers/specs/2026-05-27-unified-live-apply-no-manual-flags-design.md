# Unified Live Apply Without Manual Flags Design

## Goal

Make `dotbrowser apply` the single normal write workflow for Brave, Vivaldi,
Chrome, and Edge while a target browser may already be running. Remove the
public `--kill-browser` and `--live-port` controls rather than requiring users
to choose process-management behavior.

## User-Facing Contract

- `dotbrowser <browser> apply <config>` is sufficient for all four browsers.
- `apply` never force-kills a running browser.
- If a requested change has a supported live route, the change is sent
  through the running browser process and takes effect immediately.
- If a requested change cannot be applied live, dotbrowser asks the browser
  to close normally, performs the verified offline apply, and relaunches it.
- `--kill-browser` is removed from `apply` and `restore`.
- `--live-port` is removed from `apply`; the `launch` action is removed
  because endpoint setup becomes entirely internal to `apply`.
- `restore` is not a live-setting workflow. If the browser is running, it
  closes normally, restores the selected Preferences backup and sidecar
  state, then relaunches using its captured command line.

This intentionally does not promise that every arbitrary dotted
`[settings]` key can update without a restart. The promise is that users do
not need a force-kill flag: supported keys remain live, and unsupported keys
complete through a normal-close fallback.

## Evidence And Browser Capabilities

Brave and Vivaldi already have live adapters. Brave uses Settings WebUI,
CommandsService, and New Tab runtime actions; Vivaldi uses `vivaldi.prefs`.

Chrome and Edge were probed using temporary profiles started with local
DevTools endpoints, without accessing real user profiles:

- Both expose `chrome.settingsPrivate` from their Settings WebUI.
- In both browsers, setting `bookmark_bar.show_on_all_tabs` succeeded and
  its value changed in the running process.
- Chrome rejected `ntp.shortcust_visible` through `settingsPrivate`.
- Edge rejected `omnibox.prevent_url_elisions` through `settingsPrivate`.

Therefore Chrome and Edge can support live `[settings]` where their UI API
exposes a preference, with the shared normal-close fallback for settings
that are valid offline but not live-addressable.

## Architecture

### Orchestration

`src/dotbrowser/_base/orchestrator.py` owns process decisions:

1. Build and print plans as today, including privilege preflight for changed
   `[pwa]` policy.
2. If the target is not running, use the existing offline write/verify path.
3. If the target is running and has a live adapter, discover a remembered
   local endpoint or close normally and relaunch once with a generated
   loopback endpoint.
4. Call the adapter. If it raises `LiveApplyUnsupported`, close normally if
   still running, write/verify offline, then relaunch.
5. If the target is running without a live adapter, close normally, apply
   offline, and relaunch. Under the new design all four registered browsers
   have a live adapter, but keeping this fallback avoids coupling the
   orchestrator to today's browser list.

No branch calls `kill_fn`, reads `args.kill_browser`, or accepts a caller
provided live port. Endpoint ports remain an implementation detail stored in
`.dotbrowser.live.json` for reuse.

### Browser Adapters

- **Brave:** retain the current adapter and UI route map. Unsupported live
  routes signal `LiveApplyUnsupported` and use automatic fallback.
- **Vivaldi:** retain `vivaldi.prefs` settings/shortcut behavior. Unsupported
  live removals use automatic fallback.
- **Chrome and Edge:** add a small shared Chromium Settings WebUI adapter
  parameterized by display name and settings URL (`chrome://settings/appearance`
  or `edge://settings/appearance`). It computes changed Preferences leaves,
  preflights each one using `chrome.settingsPrivate.getPref`, applies them
  using `chrome.settingsPrivate.setPref`, writes sidecar state, and raises
  `LiveApplyUnsupported` before mutation when a key is unavailable.

Chrome and Edge continue not to expose `[shortcuts]`; this work adds live
handling only for their existing `[settings]` and policy workflows.

For all adapters, removing/resetting a managed `[settings]` key is treated as
unsupported live work and raises `LiveApplyUnsupported`, rather than emitting
an instruction to use a removed flag. The orchestrator then completes that
reset through its normal-close offline fallback.

### Restore And Process Handling

The public destructive switch is removed. For `restore`:

1. Resolve the backup and validate dry-run/list behavior as today.
2. If running, capture the main command line and request a normal close.
3. Copy the backup and clear sidecars.
4. Relaunch if a process had been closed.

`BrowserProcess.close_and_wait()` must no longer instruct users to pass
`--kill-browser`; failure to close normally reports that the user must close
the browser manually and retry.

## CLI And Documentation

Update runtime help, `README.md`, and `CLAUDE.md`:

- all four browsers are described as automatic live/fallback apply;
- no help or user workflow documents `--kill-browser`, `--live-port`, or
  `launch`;
- internal guidance states that force-kill paths are not part of the user
  workflow and that endpoint selection is private orchestration behavior;
- historical design/plan documents remain as historical records and are
  superseded by this specification.

The internal `BrowserProcess.kill_and_wait` helper may remain while shared
restore history or future maintenance paths are refactored, but no registered
CLI action can select it after this change.

## Error Handling

- MAC-protected setting validation continues before any live or offline
  mutation.
- Changed `[pwa]` privilege preflight continues before closing or relaunching
  a browser.
- Live adapter unsupported results occur before preference mutation within
  that adapter, so fallback cannot leave a partial settings update.
- Failure of a normal close stops with a manual-close error; dotbrowser never
  escalates to force-kill automatically.

## Testing

Add or update tests to cover:

- parser/help rejection and omission of `--kill-browser`, `--live-port`, and
  the `launch` action for all browser commands;
- orchestrator supported-live, endpoint auto-relaunch, unsupported-live
  fallback, and offline-only fallback without any force-kill argument;
- restore normal-close/restart behavior and removal of its kill flag;
- Chrome and Edge Settings WebUI adapters: supported settings call
  `settingsPrivate.setPref`, unsupported settings raise before backup or
  mutation;
- Brave/Vivaldi existing live behavior remains covered;
- README/CLAUDE guidance agrees with the new capability contract;
- full test suite passes after implementation.
