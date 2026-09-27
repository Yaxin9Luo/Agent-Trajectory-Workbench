# Agent Trajectory Workbench

A platform for viewing, analysing and comparing long-horizon agent trajectories for
post-training, with TypeSafe **Jev** as a cheap semantic assistant, training-data checks
and exports, and a daily rollout-reading practice on top.

Source files are never copied or modified: the Workbench indexes paths (and byte offsets
inside large JSONL files) in a SQLite database and reads transcripts on demand.

## Start

Requires Python 3.11+. Install with `uv sync` (or `pip install -e .`).

```bash
cd /Users/yaxinluo/Desktop/Agent-Trajectory-Workbench
uv run ./trajectory-workbench serve
```

Open <http://127.0.0.1:8877/>. Jev features need `TYPESAFE_API_KEY` in the server's
environment; without it everything else works and the Jev panels say so.

To run it in the background on a shared machine (pid and log under
`~/.agent-trajectory-workbench`, secrets from `~/.config/trajectory-workbench/env`):

```bash
HOST=0.0.0.0 PORT=8416 ./deploy.sh          # start / restart
PORT=8416 ./deploy.sh status
PORT=8416 ./deploy.sh stop
```

## Import trajectories

Paste an absolute path into the sidebar form, or use the CLI. A path can be a single file,
a run directory, or any directory tree; recognizable sources inside it are found
recursively. Importing a Claude Code session file also imports its `subagents/`.

```bash
./trajectory-workbench import ~/.claude/projects --collection "Claude Code sessions"
./trajectory-workbench import ~/.codex/sessions/2026/09 --collection "Codex Sept"
./trajectory-workbench import /data/sft_export/dialog.jsonl --collection "K3 SFT 0912"
./trajectory-workbench import /data/tracelab_export/k3/trajectories --collection "K3 ckpt-500"
./trajectory-workbench list
```

| Format | Recognized as | Grader verdict from |
| --- | --- | --- |
| Claude Code session `~/.claude/projects/*/*.jsonl` (+ `subagents/`), `--output-format stream-json` | file | — (harness `result` row only marks errors) |
| Codex rollout `~/.codex/sessions/**/rollout-*.jsonl` | file | — |
| ATIF `trajectory.json` (Harbor, schema checked against harbor 0.23) | file, or Harbor trial dir with `agent/trajectory.json` | `result.json` verifier reward / exception |
| OpenAI-chat / SFT `dialog.jsonl` (one sample per line, multi-GB OK) | file | sibling `manifest.json` → each input trial's `results.json` (only when the sample id matches the trial's task id) |
| TraceLab / Harbor-export trial dir (`dialog.jsonl` + `results.json`) | directory | `results.json` (MoH classification inside the exception message) |
| MoH run dir (`events.jsonl` + `attempts/`) | directory | MoH runtime classification |

A crashed or ungraded run is not a scored 0: failures are classified as agent, budget
(timeout, context or output limit), grader or infrastructure, and the reward is marked
invalid when the run crashed. Tool results without an error flag get one inferred from
their text (`Exit code N`, `<tool_use_error>`, `Process exited with code N`, an MCP
`isError` result); the reader marks inferred errors.

Use one collection per batch or checkpoint (`ckpt-400`, `ckpt-500`) so comparisons,
statistics and exports can be scoped. Every step is added to a full-text index
(~0.3 KB/step, ~6% slower import); `--no-text-index` skips it for huge exports.

## Episodes

An episode is one attempt at a task: the main trajectory, its post-compaction context
segments and every subagent it launched, transitively. They are linked from the data:
SFT sample ids (`<task>_k3_attempt_01_context_02`, `_subagent_<hash>` +
`tag.parent_tool_use_id`), Claude Code `subagents/*.meta.json` (`toolUseId`), Codex
`session_meta.source.subagent.thread_spawn.parent_thread_id` and `SubAgentActivity`
events, ATIF `subagent_trajectories`. The library, statistics and the practice queue
count episodes; the reader shows the episode strip (segments in order, missing segments
as placeholders, each subagent linked to the call that launched it).

## What you get

**Library** — one row per episode (expandable to its members) with grader outcome, model,
steps, tool errors, rule and Jev flags, training readiness and your review; filters for
outcome, flag, label, reviewed, readiness issue and member role. Tick two rows to compare.
"导出筛选结果" writes what the filters show as training data (see below).

**Reader**
- task card, grader verdict (hidden until you guess, in blind mode), episode strip;
- step rail with, underneath, a context track (prompt size per step — measured from
  Claude usage / Codex `token_count` / ATIF metrics, otherwise estimated and calibrated
  against the nearest measurement — with compaction boundaries) and a clock track (time
  to the next step); on tall screens the rail stays on top and marks what is on screen;
- signal chips that highlight their evidence steps; harness-layer counts;
- transcript loaded as a window, so `#/t/<id>?step=N` opens any step directly; long
  outputs folded to head and tail; "same command as step N" diffs the two outputs; images
  load on demand; compact mode (one line per step); training view (trained tokens of the
  model's own turns highlighted, context dimmed);
- keyboard: `j/k` steps, `J/K` model steps, `n/p` highlighted steps, `e/E` tool errors,
  `t` turning point, `a` annotate, `c` compact, `o` expand, `/` search, `[ ]` queue;
- per-step annotations (with a label) and a Markdown excerpt of chosen steps;
- **分析** tab: Jev step analysis, training readiness, other attempts at the same task,
  tool catalog, MoH panels; **台账** tab: requirement, compaction, delegation, plan/todo
  and hook-reminder ledgers; **阅读记录** tab: the review form.

**Jev assistance** (runs when a trajectory is opened; ~2–4 s and ~$0.002–0.01 each)
- one request per agent step with only that step and the tool results it reacts to:
  phase (explore / plan / implement / verify / debug / report), noticed a problem, ignored
  an error, misread a tool result (only asked when the full output is visible),
  reasoning vs action mismatch, claimed completion, harness reference, filler, violated a
  task constraint; for file-changing steps, why the files change (required / fix /
  polish / support / unclear). Code aggregates the answers into flagged steps,
  turning-point candidates, "claimed done without verifying" and the **polish tail**
  (the closing stretch that only polishes, and the step where the deliverable was done);
- final report claim vs grader verdict (overclaim / underclaim) and task ambiguity;
- ledgers: which task sentences are requirements, which step works on each, conflicts
  with harness instructions, requirements lost in each compaction summary, whether the
  parent acts on each subagent report, whether the next step responds to hook reminders;
- semantic step search and label / intervention / attribution suggestions for reviews.

**Search** — full-text search over every indexed step of a collection (Chinese phrases
work); results jump to the step with the terms highlighted.

**Tools & errors** — per-tool calls per trajectory, usage and error rate, and error
templates (the line that states the error, with paths / numbers / strings masked), for
one collection or two side by side (e.g. two checkpoints).

**Compare** — two trajectories side by side (summary, tool-usage differences, first
divergence), and **rewrite comparison**: two collections paired by sample id (before /
after a rewrite pass) with trained-token and harness-residue changes and a field-level
diff of every changed message.

**Training data**
- readiness per trajectory: over the sequence length (256K by default,
  `TRAJECTORY_WORKBENCH_MAX_SEQ_LEN`), ending cut off mid-task (not counted for segments
  that compaction continues), tool calls without results or results without calls, tool
  arguments that are not JSON, missing image files, empty turns, harness residue inside
  trained tokens (vs. only in context);
- export of the filtered trajectories: `raw` copies original JSONL lines byte for byte,
  `chat` converts any format to OpenAI chat; with a data card (filters, composition,
  readiness, token quantiles, sha256, what was skipped). From the browser, exports go to
  folders under `TRAJECTORY_WORKBENCH_EXPORT_ROOT` (default `~/trajectory-exports`);
- corrections → SFT samples and DPO pairs: a review's turning step plus its correction
  (plain text, or `{"content": …, "tool_calls": […]}`) becomes context + correction, and
  chosen = correction / rejected = what the agent did.

**Stats** — outcomes per episode; training readiness; each rule signal's prevalence among
passing vs failing episodes; Jev signal prevalence; grader × human confusion,
disagreement rate, inflated-grader rate, blind-prediction accuracy; failure labels with
how often they were decisive or severe; interventions (data, reward, eval, environment,
Skill, MCP, Instruction, Hook) and attribution (including the harness); where turning
points fall; daily activity; JSONL exports.

**Daily practice** — a stable daily mix of episodes: same-task pass/fail pairs,
cross-checkpoint pairs, suspicious passes, failures round-robin by type, and a random
control; each item says why it was picked.

**Review labels** are grouped (result authenticity, understanding & planning, execution,
finishing & verification, length & style, environment & grader, positive); each label can
carry a severity and whether it decided the outcome.

## CLI

```bash
./trajectory-workbench --db /path/index.db serve --port 8899      # separate index
./trajectory-workbench analyze --collection "K3 SFT 0912" --limit 500   # batch Jev
./trajectory-workbench export out/k3-ready --collection "K3 SFT 0912" --ready-only --mode raw
./trajectory-workbench export-reviews > reviews.jsonl
```

The default index is `~/.agent-trajectory-workbench/workbench.db`; an index from an older
version is upgraded when opened (re-import a collection, or use "重新索引" on the tools
page, to fill in fields added later). Optional environment variables:
`TRAJECTORY_WORKBENCH_REVIEWER` (review owner, default: account name),
`TRAJECTORY_WORKBENCH_JEV_MODEL` (pin a Jev version), `TRAJECTORY_WORKBENCH_JEV_CONCURRENCY`
(default 8), `TRAJECTORY_WORKBENCH_MAX_SEQ_LEN`, `TRAJECTORY_WORKBENCH_EXPORT_ROOT`.

The server has no login: bind it to 127.0.0.1, or on a shared machine only on a network
you trust. POST requests must be `application/json`, and run artifacts under `/files/`
are served in a sandboxed origin.

## Tests

```bash
PYTHONPATH=src:. uv run python -m unittest discover -s tests
node --test tests/test_presentation.mjs tests/test_rendering.mjs
```

Opt-in smoke test over real data (files or directories, any supported format):

```bash
TRAJECTORY_WORKBENCH_REAL_RUNS="/abs/one:/abs/two" PYTHONPATH=src:. uv run python -m unittest tests.test_real_run_smoke -v
```

## Adding a format

Implement `detect(path)`, `iter_runs(path)` and `load(path, locator)` (see
`src/trajectory_workbench/adapters/__init__.py`), build the transcript with
`adapters/common.py:TranscriptBuilder`, set episode keys in `meta` (`group_key`,
`self_key`, `parent_key`, `parent_call_id`, `group_role`, `segment`; `episode_scope:
"source"` when ids are only unique within one file), and register the adapter in
`DIRECTORY_ADAPTERS` or `FILE_ADAPTERS`. The UI consumes only the normalized model.
