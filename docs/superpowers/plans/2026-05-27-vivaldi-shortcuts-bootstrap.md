# Vivaldi Shortcut Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `dotbrowser vivaldi apply` and `shortcuts list` to operate on fresh profiles by loading the installed Vivaldi action defaults.

**Architecture:** Reuse `vivaldi.schema.load_schema()` as the installed-version source of truth and add shortcut-side extraction of the OS-specific `vivaldi.actions` default map. Planning treats this map as the baseline only when profile actions are empty; actual apply materializes the defaults before writing user overrides.

**Tech Stack:** Python 3.11 standard library, TOML/JSON Preferences model, pytest.

---

### Task 1: Regression Tests For Fresh Profiles

**Files:**
- Modify: `tests/test_vivaldi_logic.py`
- Modify: `tests/test_vivaldi_apply.py`

- [ ] **Step 1: Add fake action schema fixtures and failing unit tests**

Add a minimal `prefs_definitions.json` fixture containing
`vivaldi.actions.default`, `default_linux`, and `default_mac`, then assert:

```python
prefs = {"vivaldi": {}}
plan = sc.plan_apply(prefs_path, prefs, {"COMMAND_CLOSE_TAB": ["alt+x"]})
plan.apply_fn(prefs)
assert prefs["vivaldi"]["actions"][0]["COMMAND_CLOSE_TAB"]["shortcuts"] == ["alt+x"]
assert prefs["vivaldi"]["actions"][0]["COMMAND_CLOSE_TAB"]["gestures"] == ["20"]
assert plan.state_payload == {"originals": {"COMMAND_CLOSE_TAB": ["ctrl+w"]}}
```

Keep an explicit missing-schema test asserting `SystemExit` includes
`"has not seeded"`.

- [ ] **Step 2: Replace the CLI seeding-only expectations**

Point subprocess tests at a fake schema with `DOTBROWSER_VIVALDI_PREFS_DEF`.
Assert fresh-profile `apply` succeeds and `shortcuts list close` prints
`COMMAND_CLOSE_TAB`. Add a no-schema subprocess test retaining the fallback
error behavior.

- [ ] **Step 3: Run tests to verify RED**

Run:

```powershell
pytest tests/test_vivaldi_logic.py tests/test_vivaldi_apply.py -q
```

Expected: new bootstrap and schema-backed list tests fail because
`src/dotbrowser/vivaldi/shortcuts.py` still exits for empty actions.

### Task 2: Schema-Backed Shortcut Bootstrap

**Files:**
- Modify: `src/dotbrowser/vivaldi/shortcuts.py`

- [ ] **Step 1: Implement default-action loading**

Import `copy` and `dotbrowser.vivaldi.schema`. Add a helper that reads
`vivaldi.actions` from the flattened schema, selects `default_mac`,
`default_linux`, or `default`, verifies the one-element action map shape,
and returns a deep copy.

- [ ] **Step 2: Apply defaults only for uninitialized profiles**

In `plan_apply`, when profile actions are empty and config is non-empty,
use loaded defaults as `current`; preserve the existing seeding error if
loading fails. In `apply_fn`, write a fresh full default map before applying
the configured overrides when bootstrapping.

- [ ] **Step 3: Make `shortcuts list` discoverable before seeding**

For an empty action map, list names from schema defaults if present; print
the existing hint only when both profile and schema provide no catalog.

- [ ] **Step 4: Run Vivaldi tests to verify GREEN**

Run:

```powershell
pytest tests/test_vivaldi_logic.py tests/test_vivaldi_apply.py tests/test_vivaldi_schema.py -q
```

Expected: all Vivaldi tests pass.

### Task 3: Verification On The Installed Vivaldi Build

**Files:**
- Modify: `README.md` only if the feature needs user-visible clarification.

- [ ] **Step 1: Run the complete automated suite**

Run:

```powershell
pytest -q
```

Expected: no failures.

- [ ] **Step 2: Exercise the real unseeded Windows profile without writing**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m dotbrowser vivaldi shortcuts list close
python -m dotbrowser vivaldi apply C:\Users\kln\.nix\home-manager\dotfiles\browser\dotbrowser\vivaldi.toml --dry-run
```

Expected: the list contains `COMMAND_CLOSE_TAB`, and dry-run prints shortcut
diffs rather than `unknown Vivaldi command(s)` or the manual seeding error.
