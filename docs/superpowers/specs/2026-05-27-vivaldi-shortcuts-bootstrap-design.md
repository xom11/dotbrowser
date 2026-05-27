# Vivaldi Shortcut Bootstrap Design

## Problem

`dotbrowser vivaldi apply` currently validates `[shortcuts]` commands
against `vivaldi.actions[0]` in the user's `Preferences` file.  A new
Vivaldi profile does not write that preference until the user visits
Keyboard Settings, changes a shortcut, and fully quits the browser.
Consequently valid `COMMAND_*` entries fail before dotbrowser can apply
them.

## Scope

This change covers Vivaldi keyboard shortcuts only. Brave already has its
own defaults mirror and command mapping; Edge and Chrome do not expose a
supported shortcuts namespace through `Preferences`.

## Data Source

Vivaldi installs `resources/vivaldi/prefs_definitions.json`. The existing
schema loader already exposes its `vivaldi.actions` definition, including:

- `default` for Windows/default builds
- `default_linux` for Linux
- `default_mac` for macOS

Each value is a one-element action-map list with the browser's default
shortcut and gesture entries. This is the authoritative source for the
installed Vivaldi version and avoids parsing minified UI bundles.

## Behavior

When applying a non-empty `[shortcuts]` table:

1. If `Preferences` already contains a non-empty `vivaldi.actions[0]`,
   preserve the existing behavior and validate against that map.
2. If it is absent or empty, read the platform-specific default action map
   from the installed schema.
3. If defaults are available, use them as the baseline for command
   validation, diff calculation, and original-binding snapshots.
4. On a real write with changes, materialize a deep copy of the full
   default action map into `Preferences`, then apply only the configured
   shortcut overrides. This preserves unconfigured defaults and gestures.
5. If defaults cannot be loaded, retain the existing actionable error
   telling the user how to seed Vivaldi manually.

`shortcuts list` also uses schema defaults when the profile map is absent,
so discovery works before any manual Keyboard Settings interaction.

## Restore Semantics

The existing sidecar continues to store each managed command's pre-apply
shortcut list. For a bootstrap apply, those originals come from the
platform-specific defaults. Removing a key from TOML therefore restores the
real default shortcut while leaving the action's gesture data intact.

## Testing

- Unit test platform-default extraction and bootstrap apply behavior.
- Unit test the no-schema fallback still reports the manual seed guidance.
- CLI/integration test a fresh profile applies from a fake installed schema,
  writes defaults plus the override, and restores the default afterward.
- CLI test `shortcuts list` on a fresh profile resolves commands from schema.
- Run the Vivaldi test modules and full pytest suite.
- On this Windows device, run a dry-run against the actual unseeded Vivaldi
  profile and the user's TOML configuration, proving installed-schema
  discovery recognizes the commands without modifying live Preferences.
