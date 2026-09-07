export function formatDuration(milliseconds) {
  if (milliseconds == null || !Number.isFinite(Number(milliseconds))) return "—";
  const total = Number(milliseconds);
  if (total < 1000) return Math.round(total) + "ms";
  if (total < 60000) return (total / 1000).toFixed(total < 10000 ? 1 : 0) + "s";
  return Math.floor(total / 60000) + "m " + Math.round((total % 60000) / 1000) + "s";
}

export function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return value + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
  return (value / 1024 / 1024).toFixed(1) + " MB";
}

export function shortSha(value) {
  return value ? String(value).slice(0, 10) + "…" : "—";
}

export function terminalCards(run) {
  const runtime = run.runtime;
  const finalState = run.artifact_states.at(-1);
  const reason = runtime.process_terminal_reason || "unknown";
  const processCompleted = reason === "completed" && runtime.exit_code === 0;
  const processFailed = runtime.exit_code != null && runtime.exit_code !== 0;
  const processLabel = reason === "completed" && processFailed ? "failed" : reason;
  const classification = runtime.classification || "unknown";
  return [
    {
      title: "Agent " + processLabel,
      detail: "exit " + String(runtime.exit_code ?? "—") + " · " + formatDuration(run.metrics.max_offset_ms),
      state: processCompleted ? "ok" : (processFailed || !["unknown", "completed"].includes(reason) ? "bad" : "warn"),
    },
    {
      title: finalState ? "artifact recorded" : "no artifact recorded",
      detail: finalState ? formatBytes(finalState.size_bytes) + " · " + shortSha(finalState.artifact_sha256) : "—",
      state: "warn",
    },
    {
      title: "Runtime " + classification,
      detail: runtime.failure_message || runtime.agent_result_status || "—",
      state: classification === "completed" ? "ok" : (classification === "unknown" ? "warn" : "bad"),
    },
  ];
}
