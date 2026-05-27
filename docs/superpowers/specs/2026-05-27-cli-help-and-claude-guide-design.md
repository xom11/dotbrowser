# CLI Help And Claude Guide Design

## Goal

Make `dotbrowser --help` and every major subcommand help page a reliable,
self-discoverable description of the current tool, while reducing
`CLAUDE.md` to the repository guidance an implementation agent actually
needs.

## Current Problems

- Top-level help only lists browsers and does not explain the workflow or
  browser capabilities.
- Shared browser registration advertises `[shortcuts]` for Edge and Chrome,
  although they support only `[settings]` and `[pwa]`.
- Shared `apply` registration exposes `--live-port` for Edge and Chrome,
  although only Brave and Vivaldi implement live apply.
- Action and namespace help omit operational facts users need before acting:
  backup/restore scope, missing-versus-empty TOML tables, MAC refusal,
  PWA privilege requirements, URL pinning, and export limitations.
- `CLAUDE.md` repeats large sections of user-facing documentation and
  low-level history that already lives in code, tests, and `README.md`.

## Approach

Keep argparse wiring centralized in `src/dotbrowser/_base/orchestrator.py`.
Extend browser registration with explicit capabilities:

- supported TOML namespaces;
- whether live apply is available;
- browser-specific notes shown in browser-level help.

The shared registration code uses those values to generate truthful
browser/action descriptions and epilog examples. Browser modules supply
their capability values; browser-specific namespace modules retain
specialized help such as Vivaldi schema discovery and shortcut formats.

This avoids four duplicated help implementations while allowing the
differences users care about to be visible.

## Help Tree Behavior

### Root Help

`dotbrowser --help` will explain:

- purpose and command shape;
- capability matrix for Brave, Vivaldi, Edge, and Chrome;
- a minimal workflow: `init`, `apply --dry-run`, `apply`, `export`, and
  `restore`;
- where to ask for detailed browser/action help.

### Browser Help

`dotbrowser <browser> --help` will state the browser's supported TOML
tables and execution model:

- Brave: `[shortcuts]`, `[settings]`, `[pwa]`; live apply; channel flag.
- Vivaldi: `[shortcuts]`, `[settings]`, `[pwa]`; live apply; schema-backed
  `settings search` and `settings describe`.
- Edge: `[settings]`, `[pwa]`; offline apply only.
- Chrome: `[settings]`, `[pwa]`; offline apply only.

### Action Help

- `init`: explains template generation and refusal to overwrite.
- `apply`: explains config sources, table semantics, dry-run, backup,
  running-browser handling, settings MAC refusal, PWA escalation, and URL
  security flags. `--live-port` appears only for Brave/Vivaldi.
- `launch`: remains Brave/Vivaldi-only and documents that it is an advanced
  local DevTools helper.
- `export`: documents that `[settings]` is deliberately excluded; shortcut
  output differs between Brave and Vivaldi; Edge/Chrome export `[pwa]` only.
- `restore`: documents that it restores `Preferences` and clears sidecars,
  but does not restore the external PWA policy.

### Namespace Help

- `shortcuts`: identify command-name and key-format conventions, plus
  dump/list examples.
- `settings`: describe managed-key dumps, MAC-protected discovery, and
  Vivaldi-only schema operations.
- `pwa`: describe force-installed policy URLs, elevation on writes, and
  readable dump output.

## `CLAUDE.md` Redesign

Replace the current long reference with a concise maintainer guide:

- project purpose and browser capability table;
- daily commands for running, testing, and regenerating Brave command IDs;
- a file map for `_base/`, browser adapters, examples, and tests;
- invariants that must remain true when changing apply/export/live/PWA
  behavior;
- focused test guidance and release source of truth;
- links to `README.md`, `ROADMAP.md`, and existing design docs for detailed
  user or historical context.

User-facing CLI reference material belongs in `README.md` and runtime help,
not duplicated in the agent guide.

## Compatibility And Scope

- Existing command names and supported options remain unchanged except that
  Edge/Chrome no longer accept the non-functional `apply --live-port`
  option.
- No apply, export, restore, live-apply, or persistence algorithms change.
- No README rewrite is required: the existing README already contains the
  detailed user reference and matches the intended capability model.

## Testing

Add CLI help regression tests that execute the real parser and assert:

- root help contains the capability/workflow overview;
- Brave/Vivaldi browser and apply help advertise live functionality;
- Edge/Chrome help advertises only `[settings]`/`[pwa]` and rejects or does
  not document `--live-port`;
- export and restore help surface their intentional limitations;
- Vivaldi settings help continues to expose schema discovery.

Run the focused help tests first, then the complete pytest suite.
