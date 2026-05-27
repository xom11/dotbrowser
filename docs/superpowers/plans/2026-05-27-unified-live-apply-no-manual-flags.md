# Unified Live Apply Without Manual Flags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plain `apply` automatically handle running Brave, Vivaldi, Chrome, and Edge without exposing `--kill-browser`, `--live-port`, or a manual `launch` action.

**Architecture:** The shared orchestrator becomes the sole owner of endpoint creation and normal-close fallback. Brave/Vivaldi retain their live routes, while Chrome/Edge gain thin adapters over a shared Chromium Settings WebUI implementation that preflights `chrome.settingsPrivate` before mutation. Restore also switches from opt-in force-kill to normal-close/restart.

**Tech Stack:** Python 3.11 standard library, `argparse`, local DevTools Protocol client, `pytest`.

---

## File Map

- Modify `src/dotbrowser/_base/orchestrator.py`: remove public flag/action wiring; run automatic live/fallback apply and graceful restore.
- Modify `src/dotbrowser/_base/live_apply.py`: turn live setting removal into `LiveApplyUnsupported` so plain apply can fallback.
- Modify `src/dotbrowser/_base/process.py`: remove obsolete force-kill advice from normal-close errors.
- Create `src/dotbrowser/_base/chromium_live.py`: shared Settings WebUI live adapter for Chrome and Edge.
- Create `src/dotbrowser/chrome/live.py` and `src/dotbrowser/edge/live.py`: browser URL/name configuration for the shared adapter.
- Modify `src/dotbrowser/{brave,vivaldi,chrome,edge}/__init__.py`: wire only automatic apply/restore callbacks and add Chrome/Edge live adapters.
- Modify `src/dotbrowser/cli.py`, `README.md`, and `CLAUDE.md`: publish the new all-browser contract.
- Modify `tests/test_help.py`, `tests/test_live_apply.py`, `tests/test_restore.py`, and browser apply helper Namespaces: replace removed flag assumptions.
- Create `tests/test_chromium_live.py`: test Chrome/Edge Settings WebUI application and preflight fallback signaling.
- Remove `tests/test_live_launch.py`: its public action is intentionally deleted.

### Task 1: Remove Manual Process Controls From The CLI Surface

**Files:**
- Modify: `tests/test_help.py`
- Modify: `src/dotbrowser/_base/orchestrator.py`
- Modify: `src/dotbrowser/cli.py`
- Modify: `src/dotbrowser/brave/__init__.py`
- Modify: `src/dotbrowser/vivaldi/__init__.py`
- Test: `tests/test_help.py`

- [ ] **Step 1: Write failing help and parser tests**

Update help assertions to require automatic apply for all four browsers and
to prohibit the removed controls:

```python
def test_all_browser_help_advertises_automatic_running_browser_apply() -> None:
    for browser in ("brave", "vivaldi", "chrome", "edge"):
        browser_help = _help(browser)
        apply_help = _help(browser, "apply")
        assert "running" in browser_help.lower()
        assert "--kill-browser" not in apply_help
        assert "--live-port" not in apply_help
        assert "launch" not in browser_help


def test_removed_apply_flags_are_rejected() -> None:
    for flag in ("--kill-browser", "--live-port"):
        result = _run("brave", "apply", flag, "value" if flag == "--live-port" else "x.toml")
        assert result.returncode != 0
        assert "unrecognized arguments" in result.stderr
```

Use an explicit valid argument order in the actual test so the rejection is
caused by the removed option, not by a missing config.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_help.py
```

Expected: failures because `--kill-browser`, Brave/Vivaldi `--live-port`,
and `launch` are still registered and Chrome/Edge are documented offline.

- [ ] **Step 3: Remove the public CLI controls**

In `register_browser(...)`:

- replace capability-specific execution text with automatic running-browser
  apply wording for every browser;
- remove the `cmd_launch_fn` parameter and `launch` parser registration;
- remove `-k/--kill-browser` and `--live-port` from the `apply` parser;
- remove `-k/--kill-browser` from the `restore` parser.

Remove `cmd_launch` imports/functions/registration from Brave and Vivaldi,
and update the root capability overview so Chrome and Edge no longer claim
offline-only behavior.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_help.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_help.py src/dotbrowser/_base/orchestrator.py src/dotbrowser/cli.py src/dotbrowser/brave/__init__.py src/dotbrowser/vivaldi/__init__.py
git commit -m "feat(cli): remove manual browser process controls"
```

### Task 2: Make Apply And Restore Use Only Normal-Close Fallback

**Files:**
- Modify: `tests/test_live_apply.py`
- Modify: `tests/test_restore.py`
- Modify: `src/dotbrowser/_base/orchestrator.py`
- Modify: `src/dotbrowser/_base/live_apply.py`
- Modify: `src/dotbrowser/_base/process.py`
- Modify: `src/dotbrowser/{brave,vivaldi,chrome,edge}/__init__.py`
- Modify: `src/dotbrowser/brave/live.py`
- Modify: `src/dotbrowser/vivaldi/live.py`

- [ ] **Step 1: Write failing orchestration tests**

Remove `kill_browser` and `live_port` from test Namespaces. Delete tests for
explicit user-supplied live ports. Add:

```python
def test_running_browser_without_live_adapter_closes_normally_and_restarts(...):
    # live_apply_fn=None; running_fn=True
    # assert graceful_close_fn is called, Preferences is written, restart_fn is called.

def test_live_setting_removal_signals_offline_fallback() -> None:
    with pytest.raises(live_apply.LiveApplyUnsupported):
        live_apply.refuse_live_removals("Chrome", [(("foo", "bar"), live_apply.MISSING)])
```

Change the restore running-browser test to assert:

```python
monkeypatch.setattr(brave_pkg, "brave_running", lambda: True)
monkeypatch.setattr(brave_pkg.BROWSER_PROCESS, "close_and_wait", lambda: calls.append("close"))
monkeypatch.setattr(brave_pkg, "find_main_brave_cmdline", lambda: ["brave"])
monkeypatch.setattr(brave_pkg, "restart_brave", lambda cmd: calls.append(("restart", cmd)) or cmd)
_restore(profile_root)
assert calls == ["close", ("restart", ["brave"])]
```

Assert restore help no longer includes `--kill-browser`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_live_apply.py tests/test_restore.py
```

Expected: failures because the orchestrator still reads removed args,
running restore refuses without a kill flag, and setting removal exits instead
of signaling fallback.

- [ ] **Step 3: Implement automatic normal-close paths**

In `cmd_apply(...)`:

- remove `pids_fn` and `kill_fn` parameters and all reads of
  `args.kill_browser`/`args.live_port`;
- obtain endpoint ports only through `find_devtools_port` or
  `pick_unused_port`;
- on `LiveApplyUnsupported`, call `graceful_close_fn`, fall through to
  offline write, then relaunch through `launch_live_fn`;
- when called without a live adapter for a running browser, capture its
  command line, call `graceful_close_fn`, write offline, and call
  `restart_fn`.

In `refuse_live_removals(...)`, replace the `sys.exit(...)` path with:

```python
if removals:
    raise LiveApplyUnsupported(browser_name, removals)
```

In `cmd_restore(...)`, replace `pids_fn`/`kill_fn` with
`graceful_close_fn`, close normally whenever a real restore targets a
running browser, then restart if its command line was captured. Pass each
browser's `BROWSER_PROCESS.close_and_wait` callback in its wrapper.
Remove now-unused kill/PID callback imports from browser command modules.

Change `BrowserProcess.close_and_wait()` failure text to:

```python
f"error: {self.display_name} is still running after a normal close "
"request. Close it manually and retry."
```

Replace Brave/Vivaldi live-target failure strings that name `--live-port`
with endpoint-neutral live-apply errors, since endpoint management is no
longer a public flag.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_live_apply.py tests/test_restore.py tests/test_brave_live.py tests/test_vivaldi_live.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_apply.py tests/test_restore.py src/dotbrowser/_base/orchestrator.py src/dotbrowser/_base/live_apply.py src/dotbrowser/_base/process.py src/dotbrowser/brave/__init__.py src/dotbrowser/vivaldi/__init__.py src/dotbrowser/chrome/__init__.py src/dotbrowser/edge/__init__.py src/dotbrowser/brave/live.py src/dotbrowser/vivaldi/live.py
git commit -m "feat(apply): use graceful fallback without process flags"
```

### Task 3: Add Live Settings Adapters For Chrome And Edge

**Files:**
- Create: `src/dotbrowser/_base/chromium_live.py`
- Create: `src/dotbrowser/chrome/live.py`
- Create: `src/dotbrowser/edge/live.py`
- Create: `tests/test_chromium_live.py`
- Modify: `src/dotbrowser/chrome/__init__.py`
- Modify: `src/dotbrowser/edge/__init__.py`

- [ ] **Step 1: Write failing adapter tests**

Use a fake CDP client equivalent to `tests/test_brave_live.py`. Test a
supported change for each browser:

```python
@pytest.mark.parametrize(
    ("module", "settings_url"),
    [(chrome_live, "chrome://settings/appearance"),
     (edge_live, "edge://settings/appearance")],
)
def test_chromium_live_settings_use_settings_private(module, settings_url, ...):
    # Plan changes bookmark_bar.show_on_all_tabs.
    # Fake preflight evaluates to [].
    module.apply_live(9333, prefs_path, prefs, [plan])
    assert settings_url in fake.navigations
    assert any("chrome.settingsPrivate.getPref" in expr for expr in fake.evaluations)
    assert any("chrome.settingsPrivate.setPref" in expr for expr in fake.evaluations)
```

Test unsupported preflight:

```python
def test_chromium_live_rejects_unavailable_pref_before_backup(...):
    fake = FakeCdpClient(evaluation_results=[["ntp.shortcust_visible"]])
    with pytest.raises(shared_live.LiveApplyUnsupported):
        chrome_live.apply_live(9333, prefs_path, prefs, [plan])
    assert not list(prefs_path.parent.glob("Preferences.bak.*"))
    assert not any("setPref" in expr for expr in fake.evaluations)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_chromium_live.py
```

Expected: import failure because Chrome/Edge live adapters do not exist.

- [ ] **Step 3: Implement shared Settings WebUI adapter and wiring**

Create `dotbrowser._base.chromium_live.apply_live(...)` with parameters
`display_name`, `settings_url`, `port`, `prefs_path`, `prefs`, and `plans`.
It must:

1. compute target prefs and changed leaves;
2. call `refuse_live_removals(display_name, changes)`;
3. navigate to `settings_url` and evaluate a `getPref` preflight returning
   unsupported key names;
4. raise `LiveApplyUnsupported(display_name, unsupported)` before backup or
   external writes if any key is unavailable;
5. create a Preferences backup only when settings changed, apply external
   plans, invoke `settingsPrivate.setPref`, write state files, and print a
   live success message.

Create thin browser wrappers:

```python
def apply_live(port, prefs_path, prefs, plans):
    _shared.apply_live("Chrome", "chrome://settings/appearance", port, prefs_path, prefs, plans)
```

and the equivalent Edge URL. Wire `live_apply_fn`,
`graceful_close_fn=BROWSER_PROCESS.close_and_wait`, and
`launch_live_fn=BROWSER_PROCESS.launch_live` from Chrome/Edge `cmd_apply`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_chromium_live.py tests/test_chrome_apply.py tests/test_edge_apply.py tests/test_live_apply.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dotbrowser/_base/chromium_live.py src/dotbrowser/chrome/live.py src/dotbrowser/edge/live.py src/dotbrowser/chrome/__init__.py src/dotbrowser/edge/__init__.py tests/test_chromium_live.py
git commit -m "feat(chromium): live apply settings for Chrome and Edge"
```

### Task 4: Remove Stale Test Inputs And Update Guidance

**Files:**
- Modify: `tests/test_apply_live.py`
- Modify: `tests/test_settings_apply.py`
- Modify: `tests/test_unified_apply.py`
- Modify: `tests/test_error_messages.py`
- Modify: `tests/test_pwa_apply.py`
- Modify: `tests/test_vivaldi_apply.py`
- Modify: `tests/test_vivaldi_schema.py`
- Modify: `tests/test_chrome_apply.py`
- Modify: `tests/test_edge_apply.py`
- Delete: `tests/test_live_launch.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update test fixtures and docs assertions**

Remove obsolete `kill_browser=False` Namespace entries and delete
`tests/test_live_launch.py`. Add assertions in `tests/test_help.py` or a
documentation-focused test that public help contains no removed flag/action.

- [ ] **Step 2: Update documentation and repository guidance**

In `CLAUDE.md`, declare all four browsers automatic live/fallback apply,
replace the invariant that advertises `--kill-browser`, and state that
endpoint ports are internal.

In `README.md`, remove command examples/options/security prose for `-k`,
`--kill-browser`, `--live-port`, and `launch`. Explain:

- supported UI settings apply without changing the running process;
- unsupported live keys and restores use normal-close/relaunch;
- Chrome/Edge now use their Settings UI API where available and still do
  not manage shortcuts.

- [ ] **Step 3: Verify removed user-facing terms are absent from active docs/code**

Run:

```bash
rg -n -- "--kill-browser|--live-port|launch --live-port|offline apply only" README.md CLAUDE.md src/dotbrowser tests
```

Expected: no public-help/documentation occurrences; any remaining internal
identifier is intentionally non-public and reviewed.

- [ ] **Step 4: Run targeted and full verification**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q tests/test_help.py tests/test_live_apply.py tests/test_restore.py tests/test_brave_live.py tests/test_vivaldi_live.py tests/test_chromium_live.py tests/test_chrome_apply.py tests/test_edge_apply.py
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q
git diff --check
```

Expected: all tests pass and no whitespace errors.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md tests
git commit -m "docs: document automatic live apply for all browsers"
```
