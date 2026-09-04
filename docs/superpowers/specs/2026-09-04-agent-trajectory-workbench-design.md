# Agent Trajectory Workbench Design

## Goal

Build a durable local browser for MoH run records. Users register an existing run directory,
inspect the normalized trajectory, and keep the original files in place.

## Scope

Version 1 supports one input format: a MoH run directory containing run-level events plus one
attempt with immutable agent records. It does not parse native Claude Code, Codex, or TraceLab
exports. Those formats can be added later as separate adapters.

The tool is read-only with respect to imported runs. Importing a run records its absolute path and
display metadata in the Workbench registry. It never copies, rewrites, repairs, or deletes source
records.

## User flow

1. Start the local service with `trajectory-workbench serve`.
2. Paste or select an absolute MoH run directory in the browser.
3. The server validates the directory and registers a path reference.
4. The library lists available and unavailable runs.
5. Opening a run shows:
   - exact event-time lanes for messages, tools, artifact states, Workbench calls, and Runtime;
   - a searchable and filterable conversation;
   - paired tool input and result blocks;
   - deduplicated `artifact.html` states;
   - Workbench contact sheets and structured observation metadata;
   - the Agent-completed to artifact-present to Runtime-terminal chain.
6. A refresh reparses current source records, so appended or replaced immutable records are visible.

## Architecture

The codebase is a Python 3.11+ package with no runtime dependencies. A small local HTTP server owns
filesystem access and serves a modular HTML/CSS/JavaScript frontend.

The data path is:

```text
registered path
  -> MoH adapter probe and parse
  -> normalized run model
  -> JSON API
  -> browser UI
```

Adapters implement a small common interface. The UI only consumes normalized JSON and never knows
the MoH directory layout.

## Components

### Registry

The registry is a UTF-8 JSON document stored by default at
`~/.agent-trajectory-workbench/registry.json`. It stores:

- stable local ID;
- absolute source path;
- optional user-facing label;
- adapter ID;
- registration timestamp.

Registry writes use a temporary sibling plus `os.replace`. Listing checks availability without
removing missing entries.

### MoH adapter

The `moh-v1` adapter accepts a run root when these files exist:

- `events.jsonl`;
- `attempts/<attempt-id>/records/trajectory.jsonl`;
- `trajectory_index.jsonl`;
- `artifact_state_index.json`;
- `process.json`;
- `attempt_terminal.json`.

It selects the lexicographically first attempt unless a requested attempt ID is supplied. It:

- joins trajectory lines with their received offsets;
- omits `system/thinking_tokens` meter events from the conversation but counts them;
- pairs `tool_use` and `tool_result` by ID;
- deduplicates artifact observations by SHA-256;
- recognizes Slides Workbench by canonical or backend-qualified tool name;
- parses Workbench JSON when possible and preserves raw output otherwise;
- extracts the final Runtime failure from run events.

Malformed optional Workbench results degrade to raw text. Missing required files reject import with
a concrete validation error.

### HTTP API

The service binds to `127.0.0.1` by default.

- `GET /api/runs`: library entries and availability.
- `POST /api/runs`: validate and register `{"path": "...", "label": "..." }`.
- `GET /api/runs/<id>`: normalized summary, timeline, tools, artifact states, and Workbench data.
- `GET /api/runs/<id>/messages`: filtered, paginated semantic messages.
- `GET /api/runs/<id>/files/<relative-path>`: serve a file only when its resolved path stays
  inside the registered run root.
- `GET /api/health`: local readiness.

There is no delete endpoint in version 1.

### Browser UI

The interface keeps TraceLab's useful relationships while using exact MoH evidence:

- run library and path import;
- compact status and provenance header;
- linked event lanes and transcript;
- role, tool, error, and text filters;
- expandable tool input and result;
- artifact size/state history;
- Workbench contact-sheet viewer;
- explicit Runtime terminal chain.

MoH does not record each LLM request's start time, so the UI shows exact receipt points instead of
invented LLM-duration bars.

## Performance

The adapter caches normalized data in memory by the source files' modification fingerprint. It does
not write derived copies to imported run directories. Message responses are paginated; timeline
records remain compact.

## Safety

- The server listens on loopback unless a future explicit option changes it.
- Import requires an absolute directory.
- Source runs are opened read-only.
- File serving resolves and checks containment under the registered root.
- The browser cannot request arbitrary local paths.
- Raw model content is inserted with `textContent`, not trusted as HTML.

## Verification

- Unit tests cover registry atomicity, MoH probing and parsing, tool/result pairing, thinking-token
  suppression, artifact-state deduplication, and path traversal rejection.
- HTTP tests cover import, list, normalized run, message filtering, file serving, and invalid input.
- Browser smoke covers importing both existing Kimi K3 runs, switching runs, searching, filtering
  Workbench calls, opening a contact sheet, and checking console errors.

