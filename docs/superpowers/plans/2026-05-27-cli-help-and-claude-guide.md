# CLI Help And Claude Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the full CLI help tree accurately explain dotbrowser capabilities and replace the oversized agent guide with focused repository guidance.

**Architecture:** Pass browser capabilities into the shared argparse registrar so shared actions render browser-specific, accurate help without duplicated parsers. Enhance browser-specific namespace registration only where output semantics differ, and keep `CLAUDE.md` focused on development invariants and workflow.

**Tech Stack:** Python 3.11 stdlib `argparse`, pytest, Markdown.

---

### Task 1: Capture Help Requirements In Failing Tests

**Files:**
- Create: `tests/test_help.py`

- [ ] **Step 1: Add subprocess help assertions**

Create tests using `PYTHONPATH=src python -m dotbrowser ... --help` that assert:

```python
assert "Capability overview" in _run("--help").stdout
assert "[shortcuts] [settings] [pwa]" in _run("brave", "--help").stdout
assert "live apply" in _run("vivaldi", "apply", "--help").stdout.lower()
assert "[settings] [pwa]" in _run("chrome", "--help").stdout
assert "[shortcuts]" not in _run("chrome", "apply", "--help").stdout
assert "--live-port" not in _run("edge", "apply", "--help").stdout
assert "[pwa] policy is not restored" in _run("brave", "restore", "--help").stdout
```

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_help.py -q
```

Expected: failures because the current parser has neither expanded
descriptions nor capability-aware action options.

### Task 2: Make Shared Help Capability-Aware

**Files:**
- Modify: `src/dotbrowser/cli.py`
- Modify: `src/dotbrowser/_base/orchestrator.py`
- Modify: `src/dotbrowser/brave/__init__.py`
- Modify: `src/dotbrowser/vivaldi/__init__.py`
- Modify: `src/dotbrowser/edge/__init__.py`
- Modify: `src/dotbrowser/chrome/__init__.py`
- Test: `tests/test_help.py`

- [ ] **Step 1: Add formatted root help**

Use `argparse.RawDescriptionHelpFormatter` and multiline
`description`/`epilog` values in `build_parser()` for capability overview,
core workflow, and pointers to deeper help.

- [ ] **Step 2: Extend `register_browser` inputs**

Add explicit parameters for `namespaces`, `supports_live_apply`, and
`browser_notes`; create browser-level descriptions and examples from those
parameters.

- [ ] **Step 3: Expand shared action help**

Add descriptions/epilogs to `init`, `apply`, `launch`, `export`, and
`restore`. Only add `apply --live-port` when `supports_live_apply` is true;
write export text based on whether shortcut export exists.

- [ ] **Step 4: Pass truthful capability data**

Pass `("shortcuts", "settings", "pwa")` and live support from Brave/Vivaldi;
pass `("settings", "pwa")` and no live support from Edge/Chrome.

- [ ] **Step 5: Verify shared help GREEN**

Run:

```bash
pytest tests/test_help.py tests/test_smoke.py tests/test_restore.py tests/test_export.py tests/test_brave_channel.py -q
```

Expected: all selected tests pass.

### Task 3: Enrich Namespace-Specific Help

**Files:**
- Modify: `src/dotbrowser/_base/settings.py`
- Modify: `src/dotbrowser/_base/pwa.py`
- Modify: `src/dotbrowser/brave/shortcuts.py`
- Modify: `src/dotbrowser/vivaldi/shortcuts.py`
- Modify: `src/dotbrowser/vivaldi/settings.py`
- Test: `tests/test_help.py`

- [ ] **Step 1: Extend tests for namespace output**

Add assertions that shortcut help names key formats, settings help explains
managed/MAC-protected inspection, PWA help calls out managed policy, and
Vivaldi help exposes schema search/describe.

- [ ] **Step 2: Verify namespace tests RED**

Run:

```bash
pytest tests/test_help.py -q
```

Expected: the newly added namespace expectations fail.

- [ ] **Step 3: Add focused descriptions and examples**

Use raw-description formatters on namespace parsers and actions, keeping
shared wording in `_base` modules and browser-specific key/schema wording
in Brave/Vivaldi modules.

- [ ] **Step 4: Verify namespace help GREEN**

Run:

```bash
pytest tests/test_help.py -q
```

Expected: all help tests pass.

### Task 4: Replace `CLAUDE.md` With Focused Maintainer Guidance

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Reduce duplication**

Write a compact guide with purpose/capability matrix, setup and verification
commands, code map, invariants, test selection, release note, and pointers
to README/specs instead of embedding full CLI documentation.

- [ ] **Step 2: Review guide against implementation**

Check that it explicitly preserves:

```text
single Plan/orchestrator apply cycle
MAC-protected settings refusal
PWA external-write/elevation behavior
Brave/Vivaldi-only live apply
Edge/Chrome no-shortcuts limitation
export omits settings
```

### Task 5: Verification

**Files:**
- Test: `tests/test_help.py`
- Test: existing complete suite

- [ ] **Step 1: Run the full automated suite**

Run:

```bash
pytest -q
```

Expected: no failures.

- [ ] **Step 2: Inspect runtime help output**

Run:

```bash
PYTHONPATH=src python -m dotbrowser --help
PYTHONPATH=src python -m dotbrowser brave apply --help
PYTHONPATH=src python -m dotbrowser chrome apply --help
PYTHONPATH=src python -m dotbrowser vivaldi settings --help
```

Expected: readable help, accurate per-browser capability descriptions, and
no Edge/Chrome live option.
