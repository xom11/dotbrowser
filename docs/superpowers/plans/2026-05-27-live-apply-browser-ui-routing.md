# Live Apply Browser UI Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plain Brave and Vivaldi `apply` work without `--kill-browser`, while Brave New Tab preferences apply immediately through the running UI process when a live route exists.

**Architecture:** Add an explicit unsupported-live signal shared by the Brave adapter and orchestrator. Brave will preflight setting routes, apply New Tab-owned keys through `chrome://newtab/` store actions and ordinary keys through `chrome.settingsPrivate`; the orchestrator will catch unsupported-live results for plain apply and execute a normal-close offline cycle plus relaunch. Vivaldi retains its existing `vivaldi.prefs.set` implementation and receives coverage for the settings used by the real config.

**Tech Stack:** Python 3.11+ stdlib CLI, Chrome DevTools Protocol client, Brave WebUI runtime actions, Vivaldi JavaScript API, pytest.

---

## File Responsibilities

- `src/dotbrowser/_base/live_apply.py`: define the typed unsupported-live signal used before any runtime mutation.
- `src/dotbrowser/_base/orchestrator.py`: convert unsupported plain live apply into graceful offline fallback plus relaunch; refuse explicit `--live-port` fallback.
- `src/dotbrowser/brave/live.py`: classify Brave setting routes, preflight support, and invoke New Tab WebUI actions.
- `tests/test_live_apply.py`: orchestrator behavior for graceful fallback and explicit live-port refusal.
- `tests/test_brave_live.py`: Brave route/preflight behavior and existing shortcut behavior.
- `tests/test_vivaldi_live.py`: regression coverage for user's Vivaldi setting paths through existing runtime API.
- `README.md`: state that Brave uses UI routes where possible and gracefully falls back without force-killing for unsupported runtime settings.

### Task 1: Unsupported-Live Signal And Orchestrator Fallback

**Files:**
- Modify: `tests/test_live_apply.py`
- Modify: `src/dotbrowser/_base/live_apply.py`
- Modify: `src/dotbrowser/_base/orchestrator.py`

- [ ] **Step 1: Write failing orchestrator tests**

Add tests that provide a `live_apply_fn` raising the new signal:

```python
def live_apply_fn(_port, _prefs_path, _prefs, _plans):
    raise live_apply.LiveApplyUnsupported("Brave", ["foo.bar"])
```

The plain apply test must assert `graceful_close_fn` is called, `kill_fn` is
not called, `Preferences` becomes `{"foo": {"bar": 1}}`, and
`launch_live_fn` plus `remember_devtools_port` run after the offline write.
The explicit `live_port=9333` test must assert `SystemExit` mentions
`foo.bar`, does not close or kill, and leaves `Preferences` unchanged.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest \
  -q tests/test_live_apply.py
```

Expected: FAIL because `LiveApplyUnsupported` does not exist and unsupported
live application has no fallback behavior.

- [ ] **Step 3: Implement the signal and graceful fallback**

Add the signal in `src/dotbrowser/_base/live_apply.py`:

```python
class LiveApplyUnsupported(Exception):
    def __init__(self, browser_name: str, keys: list[str]):
        self.browser_name = browser_name
        self.keys = keys
        super().__init__(browser_name, keys)

    def message(self) -> str:
        joined = "\n".join(f"  {key}" for key in self.keys)
        return (
            f"error: {self.browser_name} cannot live-apply these settings:\n"
            f"{joined}"
        )
```

In `src/dotbrowser/_base/orchestrator.py`, remember whether the user supplied
`--live-port`, catch `LiveApplyUnsupported` around `live_apply_fn`, and:

```python
if requested_live_port:
    sys.exit(exc.message())
print(
    f"{display_name} cannot live-apply every requested setting; "
    "closing it normally for offline apply (no force-kill)."
)
graceful_close_fn()
relaunch_live_port = int(live_port)
```

After the normal offline write/verify cycle, relaunch with
`launch_live_fn(..., relaunch_live_port, None)`, wait for the endpoint, and
remember the port. Never invoke `kill_fn` in this fallback.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest \
  -q tests/test_live_apply.py
```

Expected: all orchestrator live tests pass.

- [ ] **Step 5: Commit the fallback unit**

```bash
git add tests/test_live_apply.py src/dotbrowser/_base/live_apply.py \
  src/dotbrowser/_base/orchestrator.py
git commit -m "feat(apply): gracefully fallback when live settings are unsupported"
```

### Task 2: Brave New Tab Runtime Routing And Preflight

**Files:**
- Modify: `tests/test_brave_live.py`
- Modify: `src/dotbrowser/brave/live.py`

- [ ] **Step 1: Write failing Brave adapter tests**

Extend `FakeCdpClient.evaluate` so probe expressions can return configurable
values. Add one test whose plan changes:

```python
target["ntp"]["shortcust_visible"] = False
target["brave"]["brave_search"]["show-ntp-search"] = False
```

Assert the generated evaluation includes:

```js
window._ntp.topSites.getState().actions.setShowTopSites(false)
window._ntp.search.getState().actions.setShowSearchBox(false)
```

and does not send either key through `chrome.settingsPrivate.setPref`.

Add a second test for an unrecognized setting where the fake returns
`["browser.unknown_setting"]` from the `getPref` preflight; assert
`LiveApplyUnsupported` is raised before any expression includes
`settingsPrivate.setPref`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest \
  -q tests/test_brave_live.py
```

Expected: FAIL because New Tab settings are currently sent to
`settingsPrivate` and no preflight signal is raised.

- [ ] **Step 3: Implement Brave route classification and scripts**

In `src/dotbrowser/brave/live.py`, add:

```python
_NEWTAB_URL = "chrome://newtab/"
_NEWTAB_ACTIONS = {
    "ntp.shortcust_visible": ("topSites", "setShowTopSites"),
    "brave.brave_search.show-ntp-search": ("search", "setShowSearchBox"),
    "brave.brave_search.show-ntp-chat": ("search", "setShowChatInput"),
    "brave.new_tab_page.show_background_image": ("background", "setBackgroundsEnabled"),
    "brave.new_tab_page.show_branded_background_image": ("background", "setSponsoredImagesEnabled"),
    "brave.new_tab_page.show_clock": ("newTab", "setShowClock"),
    "brave.new_tab_page.show_stats": ("newTab", "setShowShieldsStats"),
    "brave.new_tab_page.show_rewards": ("rewards", "setShowRewardsWidget"),
    "brave.new_tab_page.show_brave_vpn": ("vpn", "setShowVpnWidget"),
    "brave.new_tab_page.show_together": ("newTab", "setShowTalkWidget"),
}
```

Split `_setting_changes(...)` into New Tab-routed and
`settingsPrivate`-routed values. Add a `getPref` preflight expression for
ordinary keys and an action-presence preflight expression for New Tab keys;
if either returns unsupported keys, raise:

```python
raise _live.LiveApplyUnsupported("Brave", unsupported)
```

Only after preflight succeeds should `apply_live` call
`backup_preferences`, external apply functions, the New Tab action script,
ordinary settings script, and shortcut script.

- [ ] **Step 4: Run Brave tests to verify GREEN**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest \
  -q tests/test_brave_live.py tests/test_live_apply.py
```

Expected: all Brave adapter and orchestrator live tests pass.

- [ ] **Step 5: Commit the Brave routing unit**

```bash
git add tests/test_brave_live.py src/dotbrowser/brave/live.py
git commit -m "feat(brave): apply New Tab preferences through live UI routes"
```

### Task 3: Vivaldi Contract Coverage

**Files:**
- Modify: `tests/test_vivaldi_live.py`

- [ ] **Step 1: Extend the existing Vivaldi live test**

Include the user's settings in the test profile and `apply_fn`:

```python
"panels": {"position": 0},
"auto_hide": {"enabled": False},
```

```python
target["vivaldi"]["panels"]["position"] = 1
target["vivaldi"]["auto_hide"]["enabled"] = True
```

Assert expressions sent through `vivaldi.prefs.set` include
`vivaldi.panels.position` and `vivaldi.auto_hide.enabled`, while the existing
shortcut assertion still verifies the internal UI reload.

- [ ] **Step 2: Run Vivaldi test as characterization coverage**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest \
  -q tests/test_vivaldi_live.py
```

Expected: PASS because the existing generic `vivaldi.prefs.set` route already
supports these settings; no Vivaldi production-code change is required.

- [ ] **Step 3: Commit Vivaldi coverage**

```bash
git add tests/test_vivaldi_live.py
git commit -m "test(vivaldi): cover live apply for configured settings"
```

### Task 4: Documentation And Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update live apply documentation**

In the live apply section, document that Brave New Tab-owned preferences are
applied through the running New Tab UI APIs, ordinary live settings through
Settings APIs, and a preference without a known runtime route causes plain
`apply` to close normally, apply offline, and relaunch without force-killing.
Retain the statement that `--kill-browser` explicitly chooses the force-kill
path.

- [ ] **Step 2: Run targeted verification**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest \
  -q tests/test_live_apply.py tests/test_brave_live.py tests/test_vivaldi_live.py \
  tests/test_help.py
```

Expected: PASS.

- [ ] **Step 3: Run full verification**

Run:

```bash
PYTHONPATH=src uv run --isolated --no-project --with pytest python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md
git commit -m "docs: describe runtime live apply fallback behavior"
```
