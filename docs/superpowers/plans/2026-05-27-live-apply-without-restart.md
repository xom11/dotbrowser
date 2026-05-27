# Live Apply Without Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plain `apply` update Brave/Vivaldi settings and shortcuts through the running browser automatically, without users needing `launch`, `--live-port`, or `--kill-browser`.

**Architecture:** Keep existing TOML planning and diff generation. Add a shared CDP client plus browser-specific live adapters that translate computed target preferences into internal browser API calls.

**Tech Stack:** Python stdlib HTTP/socket/WebSocket client, argparse, pytest, existing dotbrowser `Plan` contract.

---

### Task 1: Live Orchestrator Entry

**Files:**
- Modify: `src/dotbrowser/_base/orchestrator.py`
- Test: `tests/test_live_apply.py`

- [ ] Add `--live-port PORT` to the `apply` parser and reject it with `--kill-browser`.
- [ ] Let `cmd_apply` accept an optional `live_apply_fn`.
- [ ] When the browser is running, auto-discover an existing live port or graceful-relaunch once, then call `live_apply_fn(port, prefs_path, prefs, plans)` instead of killing or writing `Preferences`.
- [ ] Preserve dry-run behavior and normal diff output.

### Task 1.5: Live Launch Helper

**Files:**
- Modify: `src/dotbrowser/_base/orchestrator.py`
- Modify: `src/dotbrowser/_base/process.py`
- Modify: `src/dotbrowser/brave/__init__.py`
- Modify: `src/dotbrowser/vivaldi/__init__.py`
- Test: `tests/test_live_launch.py`

- [ ] Add `launch --live-port PORT [url]` for Brave and Vivaldi.
- [ ] Refuse launch when the browser is already running.
- [ ] Start with `--remote-debugging-address=127.0.0.1`, `--remote-debugging-port=PORT`, `--user-data-dir`, and `--profile-directory`.
- [ ] Keep `launch` as an advanced helper; plain `apply` should not require it.

### Task 2: CDP Client

**Files:**
- Create: `src/dotbrowser/_base/cdp.py`
- Test: `tests/test_cdp.py`

- [ ] Add HTTP helpers for `/json/list`.
- [ ] Add a minimal stdlib WebSocket client that can send `Runtime.evaluate`, `Page.navigate`, `Page.reload`, and `Browser.close`.
- [ ] Surface connection failures as `SystemExit` messages with the requested port.

### Task 3: Live Apply Helpers

**Files:**
- Create: `src/dotbrowser/_base/live_apply.py`
- Test: `tests/test_live_apply.py`

- [ ] Add a helper that deep-copies prefs and runs every non-empty `Plan.apply_fn` to compute target prefs.
- [ ] Add recursive diff helpers for changed settings leaves.
- [ ] Add a helper that writes sidecar state files after browser API application.

### Task 4: Brave Adapter

**Files:**
- Create: `src/dotbrowser/brave/live.py`
- Modify: `src/dotbrowser/brave/__init__.py`
- Test: `tests/test_brave_live.py`

- [ ] Apply changed settings through `chrome.settingsPrivate.setPref`.
- [ ] Apply changed shortcuts through `/commands.bundle.js` `commandsCache.assignAccelerator` and `unassignAccelerator`.
- [ ] Wire the adapter into `brave.cmd_apply`.

### Task 5: Vivaldi Adapter

**Files:**
- Create: `src/dotbrowser/vivaldi/live.py`
- Modify: `src/dotbrowser/vivaldi/__init__.py`
- Test: `tests/test_vivaldi_live.py`

- [ ] Apply changed settings through `vivaldi.prefs.set`.
- [ ] Apply changed `vivaldi.actions` through `vivaldi.prefs.set`.
- [ ] Reload Vivaldi internal UI target after shortcut changes.
- [ ] Wire the adapter into `vivaldi.cmd_apply`.

### Task 6: Docs And Verification

**Files:**
- Modify: `README.md`

- [ ] Document `--live-port`, its security implications, and the need to start the browser with `--remote-debugging-address=127.0.0.1 --remote-debugging-port=PORT`.
- [ ] Run targeted RED/GREEN tests.
- [ ] Run the full pytest suite.
