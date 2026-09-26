# Agent Trajectory Workbench

Local, read-only browser for MoH agent trajectories.

The Workbench registers source directories by absolute path. It does not copy or modify run
records.

## Start

Requires Python 3.11 or newer. Uses `typesafe-sdk` for Jev semantic analysis. Install with `uv sync` or `pip install -e .`.

```bash
cd /Users/yaxinluo/Desktop/Agent-Trajectory-Workbench
uv run ./trajectory-workbench serve
```

Open [http://127.0.0.1:8877/](http://127.0.0.1:8877/).

Paste an absolute MoH run root into the left-side import form. The directory must contain
`events.jsonl` and `attempts/<attempt-id>/records/`.

## CLI import

```bash
./trajectory-workbench import /absolute/path/to/run --label "My run"
./trajectory-workbench list
```

The default registry is `~/.agent-trajectory-workbench/registry.json`. Use a separate registry
when testing:

```bash
./trajectory-workbench --registry ./local-registry.json import /absolute/path/to/run
./trajectory-workbench --registry ./local-registry.json serve --port 8899
```

An editable Python install is optional when the environment already provides a compatible
`setuptools`: `python3 -m pip install -e .`.

If a referenced directory moves or disappears, the library keeps the entry and marks it
unavailable.

## Current capabilities

- MoH `moh-v1` run validation and normalization;
- exact message, tool, artifact, Workbench, and Runtime event lanes;
- tool inventory combining the session's advertised surface, manifest declarations, and observed calls;
- searchable and filterable semantic transcript;
- generic paired tool inputs, results, and error state for arbitrary tool names;
- deduplicated `artifact.html` state history with content diffs;
- Slides Workbench contact-sheet inspection;
- explicit Agent/process/artifact/Runtime terminal status;
- paginated messages and in-memory source-fingerprint cache;
- **Jev semantic analysis** — TypeOne model scores run health, error severity, and failure categories with calibrated confidence;
- **error/retry aggregation** — groups tool failures by name and tracks recovery chains;
- **artifact content diff** — line-level added/removed counts between consecutive artifact states;
- **cross-run comparison** — side-by-side metrics and tool-usage diff between any two runs.

The UI does not invent LLM start times or call durations that are absent from MoH records.
Missing tool durations display as `—`; recorded zero remains `0ms`. Terminal cards distinguish
process completion, timeout, recorded artifact history, and Runtime finalization. Missing terminal
evidence stays unknown. Workbench failure cards expose the recorded error code and message.
The exact MoH alias `mcp__generate_image__generate_image` joins `generate_image` in the tool
inventory while retaining its raw name; unrelated MCP servers remain distinct.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --test tests/test_presentation.mjs
```

Presentation tests use Node.js 18 or newer; the running application still needs only Python.

Run an opt-in smoke against real MoH directories:

```bash
TRAJECTORY_WORKBENCH_REAL_RUNS="/absolute/run-one:/absolute/run-two" \
  PYTHONPATH=src python3 -m unittest tests.test_real_run_smoke -v
```

## Adding another trace format

Implement the `TrajectoryAdapter` protocol in
`src/trajectory_workbench/adapters/base.py`, then register the adapter in the service. The
frontend consumes the normalized model and does not depend on the MoH directory layout.
