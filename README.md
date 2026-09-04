# Agent Trajectory Workbench

Local, read-only browser for MoH agent trajectories.

The Workbench registers source directories by absolute path. It does not copy or modify run
records.

## Start

Requires Python 3.11 or newer. There are no runtime dependencies.

```bash
cd /Users/yaxinluo/Desktop/Agent-Trajectory-Workbench
./trajectory-workbench serve
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
- deduplicated `artifact.html` state history;
- Slides Workbench contact-sheet inspection;
- explicit Agent/process/artifact/Runtime terminal status;
- paginated messages and in-memory source-fingerprint cache.

The UI does not invent LLM start times or call durations that are absent from MoH records.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Run an opt-in smoke against real MoH directories:

```bash
TRAJECTORY_WORKBENCH_REAL_RUNS="/absolute/run-one:/absolute/run-two" \
  PYTHONPATH=src python3 -m unittest tests.test_real_run_smoke -v
```

## Adding another trace format

Implement the `TrajectoryAdapter` protocol in
`src/trajectory_workbench/adapters/base.py`, then register the adapter in the service. The
frontend consumes the normalized model and does not depend on the MoH directory layout.
