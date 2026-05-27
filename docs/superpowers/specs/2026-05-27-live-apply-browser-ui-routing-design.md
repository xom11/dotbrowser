# Live Apply Browser UI Routing Design

## Goal

Make normal `dotbrowser brave apply CONFIG` and
`dotbrowser vivaldi apply CONFIG` succeed without requiring
`--kill-browser`.

For supported live changes, the running browser process must remain alive and
the change must take effect immediately, as if made through the browser UI.
When a Brave preference has no known live route, plain `apply` must remain
usable by falling back to a normal close, offline write, and relaunch rather
than failing after a partial live apply or requiring a force-kill.

## Current Behavior And Problem

Vivaldi already sends settings through `vivaldi.prefs.set` and shortcuts
through `vivaldi.actions`, reloading only its internal UI after shortcut
changes.

Brave currently sends all setting changes through
`chrome.settingsPrivate.setPref`. This works for standard Brave settings but
does not cover settings owned by the New Tab Page WebUI. For example, the
user's live apply failed on:

```text
Pref not found: ntp.shortcust_visible
```

Investigation against a running Brave instance showed that its New Tab Page
uses a different live route:

```js
window._ntp.topSites.getState().actions.setShowTopSites(false)
```

Calling this route set `ntp.shortcust_visible = false` while keeping the same
Brave process running and immediately hiding top sites.

## Design

### Brave Live Routing

Extend `dotbrowser.brave.live` to split setting changes into routes before
mutating the browser:

| Preference path | Live route |
|---|---|
| `ntp.shortcust_visible` | New Tab `topSites.actions.setShowTopSites` |
| `brave.brave_search.show-ntp-search` | New Tab `search.actions.setShowSearchBox` |
| `brave.brave_search.show-ntp-chat` | New Tab `search.actions.setShowChatInput` |
| `brave.new_tab_page.show_background_image` | New Tab `background.actions.setBackgroundsEnabled` |
| `brave.new_tab_page.show_branded_background_image` | New Tab `background.actions.setSponsoredImagesEnabled` |
| `brave.new_tab_page.show_clock` | New Tab `newTab.actions.setShowClock` |
| `brave.new_tab_page.show_stats` | New Tab `newTab.actions.setShowShieldsStats` |
| `brave.new_tab_page.show_rewards` | New Tab `rewards.actions.setShowRewardsWidget` |
| `brave.new_tab_page.show_brave_vpn` | New Tab `vpn.actions.setShowVpnWidget` |
| `brave.new_tab_page.show_together` | New Tab `newTab.actions.setShowTalkWidget` |
| Other live-readable settings | Existing `chrome.settingsPrivate.setPref` route |

The adapter navigates a page target to `chrome://newtab/` only when New Tab
routes are needed, waits for `window._ntp` stores to initialize, then invokes
the configured actions. It uses `chrome://settings/appearance` only for
settings handled by `settingsPrivate`, and continues using
`chrome://settings/system/shortcuts` for shortcuts.

### Preflight And Fallback

Live apply must not partially apply settings and then discover that a later
key is unsupported. Before applying anything, Brave preflights the planned
setting changes:

- Known New Tab keys are considered supported by their explicit route.
- Remaining keys are probed through `chrome.settingsPrivate.getPref`.
- If all probes succeed, proceed with live setting and shortcut application.
- If any changed setting is unsupported, signal to the orchestrator that this
  plan requires offline apply.

For a plain `apply` invocation, the orchestrator handles that signal by:

1. Requesting a normal browser close through the existing
   `graceful_close_fn`.
2. Performing the ordinary backup/write/verify offline cycle.
3. Relaunching the browser with a local DevTools endpoint so later supported
   live applies retain the normal fast path.

This fallback must never call `kill_fn`. Explicit `--live-port` remains a
debug assertion of live capability: if it names unsupported live settings,
the command errors before mutation instead of silently closing the endpoint's
browser.

### Vivaldi

Vivaldi does not need new routing: its current adapter already uses
`vivaldi.prefs.set` for arbitrary planned settings and `vivaldi.actions` for
shortcuts. Extend coverage around settings represented in the user's config
to protect the no-force-kill contract and retain internal UI reload behavior
for shortcut changes.

### State, Backup, And Failure Handling

- Live apply writes sidecar state only after all runtime mutations succeed.
- Live preflight runs before creating a live-path backup or invoking any
  external apply callback.
- A successful Brave New Tab route uses the existing live backup behavior.
- Graceful offline fallback uses the existing one-backup offline cycle.
- A failed preflight leaves browser state, sidecar state, and preference files
  untouched.

## Testing

Add focused automated tests for:

- Brave splits `ntp.shortcust_visible` and other known New Tab preference
  paths away from `settingsPrivate` and invokes New Tab runtime actions.
- Brave preflight accepts `settingsPrivate`-supported settings and refuses an
  unsupported unknown live setting before mutation.
- Plain Brave `apply` responds to unsupported-live preflight with graceful
  offline apply plus relaunch, without invoking `kill_fn`.
- `--live-port` with unsupported-live settings refuses before mutation.
- Existing Brave shortcut live apply remains intact alongside New Tab
  routing.
- Vivaldi setting and shortcut live application uses `vivaldi.prefs.set`,
  performs the existing UI reload for shortcuts, and does not invoke a kill
  path.

Run targeted live suites and the full test suite after implementation.

