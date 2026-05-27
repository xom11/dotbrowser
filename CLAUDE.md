# CLAUDE.md

Guidance for agents changing this repository. User-facing setup, examples,
and CLI reference belong in `README.md` and runtime `--help`; keep this file
focused on implementation constraints.

## Project

`dotbrowser` manages Chromium-based browser customizations from TOML files.
It is a Python 3.11+ stdlib-only CLI package.

| Browser | Managed TOML tables | Apply mode |
|---|---|---|
| Brave | `[shortcuts]`, `[settings]`, `[pwa]` | offline or live; stable/beta/nightly |
| Vivaldi | `[shortcuts]`, `[settings]`, `[pwa]` | offline or live; schema-aware settings |
| Edge | `[settings]`, `[pwa]` | offline only |
| Chrome | `[settings]`, `[pwa]` | offline only |

Edge and Chrome do not expose a supported keyboard-shortcut preference
surface. Do not advertise or implement `[shortcuts]` for them without a
verified persistence mechanism.

## Commands

Run from the repository root:

```bash
pip install -e ".[test]"
PYTHONPATH=src python -m dotbrowser --help
PYTHONPATH=src python -m dotbrowser brave apply --help
pytest -q

# Regenerate Brave's command-name mapping from upstream headers.
# Requires an authenticated `gh` CLI.
python scripts/generate_brave_command_ids.py
```

Useful targeted suites:

```bash
pytest tests/test_help.py tests/test_smoke.py
pytest tests/test_unified_apply.py tests/test_settings_apply.py tests/test_pwa_apply.py
pytest tests/test_live_apply.py tests/test_brave_live.py tests/test_vivaldi_live.py
pytest tests/test_edge_apply.py tests/test_chrome_apply.py tests/test_export.py
pytest tests/test_vivaldi_apply.py tests/test_vivaldi_schema.py
```

## Code Map

- `src/dotbrowser/cli.py`: root parser and browser registration.
- `src/dotbrowser/_base/orchestrator.py`: config loading, unified
  `apply`/`init`/`export`/`restore`/`launch`, and shared argparse wiring.
- `src/dotbrowser/_base/utils.py`: `Plan`, Preferences loading, atomic write.
- `src/dotbrowser/_base/settings.py`: dotted-key settings and MAC refusal.
- `src/dotbrowser/_base/pwa.py`: policy validation, read/write, and diff logic.
- `src/dotbrowser/_base/process.py`, `cdp.py`, `live_apply.py`: browser
  lifecycle and live-apply infrastructure.
- `src/dotbrowser/{brave,vivaldi,edge,chrome}/`: browser capability and
  platform adapters. Brave/Vivaldi contain shortcut-specific logic;
  Brave/Vivaldi also wire live adapters.
- `examples/<browser>/`: valid user-facing config samples.
- `tests/`: behavior contracts. Add or update tests alongside behavior or
  CLI-help changes.

## Invariants

Preserve these contracts unless a change explicitly redesigns them:

1. `apply` uses module `Plan` objects and one orchestrated cycle. Validate
   all selected namespaces before committing profile changes; create at most
   one Preferences backup per offline apply.
2. Missing TOML table means "skip this namespace"; an empty table means
   "remove/reset entries previously managed by dotbrowser".
3. `[settings]` must refuse MAC-protected keys found in either `Preferences`
   or sibling `Secure Preferences`. Never make a write that Chromium will
   silently reset on launch.
4. `[pwa]` is external managed policy storage. It requires sudo on
   Linux/macOS or Administrator on Windows when changed, writes before the
   Preferences commit, and has no Preferences sidecar.
5. Live apply exists only for Brave and Vivaldi. Endpoints bind to
   `127.0.0.1`; `--kill-browser` forces the offline path. Live removal/reset
   of settings is not supported yet.
6. `export` intentionally omits `[settings]`. Brave exports shortcut diffs
   against stored defaults; Vivaldi exports non-empty bindings because it
   has no defaults mirror; Edge/Chrome export `[pwa]` only.
7. `restore` restores Preferences backups and clears shortcut/settings
   sidecars. It does not roll back external `[pwa]` policy.
8. Runtime help is part of the capability contract. Shared parser wording
   must remain truthful for each browser; do not expose options such as live
   apply where no implementation exists.

## Browser-Specific Notes

- Brave shortcut values use Chromium KeyEvent-style bindings. `Meta+` and
  `Command+` are normalized per platform before persistence.
- Brave `--channel` changes both profile discovery and process handling.
  Non-stable Linux channels require PID filtering so applying Beta/Nightly
  does not kill another Brave channel.
- Vivaldi shortcut keys are `COMMAND_*` names stored under
  `vivaldi.actions`; original bindings are recorded for restore-on-removal.
- Vivaldi may read its installed `prefs_definitions.json` for shortcut
  bootstrap and for settings enum validation/search/describe. The override
  variable is `DOTBROWSER_VIVALDI_PREFS_DEF`.

## Changing The CLI Or A Browser

- Keep shared behavior in `_base/`; browser packages should configure or
  adapt it rather than fork orchestration.
- Pass accurate capabilities through `register_browser(...)` so help,
  action options, and implementation agree.
- For a new Chromium browser, provide profile/process configuration,
  thin settings and PWA wrappers, browser registration, examples, apply
  tests, export coverage, and help coverage. Add shortcuts or live apply
  only when the browser exposes a tested API.
- Preserve testability: policy paths, privilege writers, and process
  callbacks are intentionally patchable in tests.

## Release

`src/dotbrowser/__init__.py::__version__` is the version source of truth;
`pyproject.toml` reads it through Hatch. Releases are tag-driven through
`.github/workflows/release.yml` after tests pass.

## References

- `README.md`: user workflow and complete CLI/reference documentation.
- `ROADMAP.md`: deferred work and known limitations.
- `docs/superpowers/specs/` and `docs/superpowers/plans/`: design decisions
  and implementation plans for substantial changes.
