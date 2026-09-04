# Agent Trajectory Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable local application that registers MoH run directories by reference and
provides an evidence-grounded trajectory browser.

**Architecture:** A dependency-free Python package validates and normalizes MoH records, exposes a
loopback JSON API, and serves a modular static frontend. The adapter boundary isolates source
formats from the browser model.

**Tech Stack:** Python 3.11 standard library, `unittest`, HTML5, CSS, browser-native JavaScript.

**Spec:** `docs/superpowers/specs/2026-09-04-agent-trajectory-workbench-design.md`

## Global Constraints

- Import stores an absolute path reference and never copies or mutates source records.
- Version 1 supports only the `moh-v1` adapter.
- The default bind address is `127.0.0.1`.
- Runtime dependencies remain Python standard-library only.
- File access must remain contained inside registered run roots.
- The UI must not invent LLM start times or per-call durations absent from MoH records.

---

### Task 1: Registry and normalized MoH adapter

**Files:**
- Create: `pyproject.toml`
- Create: `src/trajectory_workbench/__init__.py`
- Create: `src/trajectory_workbench/models.py`
- Create: `src/trajectory_workbench/registry.py`
- Create: `src/trajectory_workbench/adapters/__init__.py`
- Create: `src/trajectory_workbench/adapters/base.py`
- Create: `src/trajectory_workbench/adapters/moh_v1.py`
- Create: `tests/test_registry.py`
- Create: `tests/test_moh_adapter.py`

**Interfaces:**
- Produces: `Registry.register(path: Path, label: str | None) -> RegistryEntry`
- Produces: `Registry.list_entries() -> list[RegistryEntry]`
- Produces: `MohV1Adapter.probe(path: Path) -> ProbeResult`
- Produces: `MohV1Adapter.load(path: Path) -> NormalizedRun`

- [ ] **Step 1: Write failing registry and adapter behavior tests**

Create temporary MoH fixtures with one assistant tool call, its user tool result, thinking-token
meter rows, duplicate artifact SHAs, process data, and a run failure event. Assert that registration
stores the resolved path, parsing pairs tool results, meter rows are counted but hidden, and states
are deduplicated.

- [ ] **Step 2: Run tests and confirm the missing-package failure**

Run: `python3 -m unittest tests.test_registry tests.test_moh_adapter -v`

Expected: import failure for `trajectory_workbench`.

- [ ] **Step 3: Implement the package models, atomic registry, and MoH adapter**

Use dataclasses for registry/probe results. Return JSON-ready dictionaries from the normalized run.
Index trajectory offsets by `line_number`, and match tool results by `tool_use_id`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_registry tests.test_moh_adapter -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src tests
git commit -m "feat: add MoH trajectory adapter"
```

### Task 2: Loopback API and safe file serving

**Files:**
- Create: `src/trajectory_workbench/service.py`
- Create: `src/trajectory_workbench/server.py`
- Create: `src/trajectory_workbench/__main__.py`
- Create: `tests/test_service.py`
- Create: `tests/test_server.py`

**Interfaces:**
- Consumes: `Registry`, `MohV1Adapter`, and `NormalizedRun`
- Produces: `WorkbenchService.import_run(path, label) -> dict`
- Produces: `WorkbenchService.get_run(run_id) -> dict`
- Produces: `WorkbenchService.get_messages(run_id, filters) -> dict`
- Produces: `create_server(host, port, registry_path) -> ThreadingHTTPServer`

- [ ] **Step 1: Write failing service and HTTP tests**

Assert successful import/list/load, unavailable-path reporting, role/tool/search pagination, 400 for
invalid imports, 404 for unknown IDs, and 403 for `../` file traversal.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python3 -m unittest tests.test_service tests.test_server -v`

Expected: missing service/server modules.

- [ ] **Step 3: Implement the service, router, CLI, and containment checks**

Parse JSON request bodies with a size ceiling. Map domain validation errors to 400 and unknown
entries to 404. Resolve requested files and require `candidate.is_relative_to(run_root)`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_service tests.test_server -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: expose local trajectory API"
```

### Task 3: Reusable trajectory browser

**Files:**
- Create: `src/trajectory_workbench/web/index.html`
- Create: `src/trajectory_workbench/web/styles.css`
- Create: `src/trajectory_workbench/web/api.js`
- Create: `src/trajectory_workbench/web/app.js`
- Create: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `GET/POST /api/runs`, normalized run JSON, paginated message JSON, safe file URLs
- Produces: run library, linked event lanes, searchable transcript, artifact state view, Workbench
  viewer, and terminal-chain view

- [ ] **Step 1: Write failing web-asset contract tests**

Assert that package assets exist, API calls use relative URLs, source content is rendered through
`textContent`, and required controls have accessible labels.

- [ ] **Step 2: Run the focused test**

Run: `python3 -m unittest tests.test_web_assets -v`

Expected: failure because the web assets do not exist.

- [ ] **Step 3: Implement the frontend as small modules**

Port the proven prototype interactions. Keep the run library and import form in a left rail, the
timeline and transcript in the main column, and Slides/Runtime evidence in a right inspector.

- [ ] **Step 4: Run the focused test**

Run: `python3 -m unittest tests.test_web_assets -v`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/trajectory_workbench/web tests/test_web_assets.py
git commit -m "feat: add trajectory browser interface"
```

### Task 4: Real-run smoke, documentation, and handoff

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Create: `tests/test_real_run_smoke.py`

**Interfaces:**
- Consumes: the two explicitly supplied Kimi K3 MoH run paths
- Produces: documented start/import commands and browser-verified local application

- [ ] **Step 1: Add an opt-in real-run smoke test**

Read run paths from `TRAJECTORY_WORKBENCH_REAL_RUNS`. For each supplied path, probe and normalize
it, asserting at least one tool call, artifact state, message, and two Workbench observations.

- [ ] **Step 2: Add concise usage and architecture documentation**

Document editable installation, server startup, CLI import, browser import, registry location,
source-read-only behavior, supported format, and test commands.

- [ ] **Step 3: Run deterministic verification**

Run: `python3 -m unittest discover -s tests -v`

Expected: all deterministic tests pass; real smoke skips without its environment variable.

- [ ] **Step 4: Run the two real Kimi K3 cases**

Run:

```bash
TRAJECTORY_WORKBENCH_REAL_RUNS="/absolute/culture/run:/absolute/roster/run" \
  python3 -m unittest tests.test_real_run_smoke -v
```

Expected: two valid normalized runs with the recorded tool/state/Workbench evidence.

- [ ] **Step 5: Browser smoke**

Start the service, register both run directories, and verify run switching, text search,
Workbench-only filtering, contact-sheet expansion, missing-run status, and an empty browser console.

- [ ] **Step 6: Review and commit**

```bash
git diff --check
python3 -m compileall -q src
git add .
git commit -m "docs: finish local trajectory workbench"
```

