const API_ROOT = "/api";
const ALL_COLLECTIONS = "*";

async function request(path, options = {}) {
  const response = await fetch(API_ROOT + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch (_) {
      // Keep the HTTP status text.
    }
    throw new Error(message);
  }
  return response.json();
}

function query(params) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, Array.isArray(value) ? value.join(",") : String(value));
  });
  const text = search.toString();
  return text ? "?" + text : "";
}

const id = (value) => encodeURIComponent(value);
const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body || {}) });

export const api = {
  collections: () => request("/collections"),
  taxonomy: () => request("/taxonomy"),
  trajectories: (filters) => request("/trajectories" + query(filters)),
  trajectory: (trajectoryId) => request("/trajectories/" + id(trajectoryId)),
  messages: (trajectoryId, filters) => request("/trajectories/" + id(trajectoryId) + "/messages" + query(filters)),
  episode: (trajectoryId) => request("/trajectories/" + id(trajectoryId) + "/episode"),
  annotations: (trajectoryId) => request("/trajectories/" + id(trajectoryId) + "/annotations"),
  saveAnnotation: (trajectoryId, fields) => post("/trajectories/" + id(trajectoryId) + "/annotations", fields),
  deleteAnnotation: (trajectoryId, annotationId) =>
    request("/trajectories/" + id(trajectoryId) + "/annotations/" + id(annotationId), { method: "DELETE" }),
  excerptUrl: (trajectoryId, steps, hideOutcome) =>
    API_ROOT + "/trajectories/" + id(trajectoryId) + "/excerpt.md" + query({ steps, hide_outcome: hideOutcome ? 1 : "" }),
  ledgers: (trajectoryId) => request("/trajectories/" + id(trajectoryId) + "/ledgers"),
  runLedgers: (trajectoryId, force = false) => post("/trajectories/" + id(trajectoryId) + "/ledgers", { force }),
  errors: (trajectoryId) => request("/trajectories/" + id(trajectoryId) + "/errors"),
  artifactDiffs: (trajectoryId) => request("/trajectories/" + id(trajectoryId) + "/artifact-diffs"),
  saveReview: (trajectoryId, fields) => post("/trajectories/" + id(trajectoryId) + "/review", fields),
  deleteReview: (trajectoryId) => request("/trajectories/" + id(trajectoryId) + "/review", { method: "DELETE" }),
  suggest: (trajectoryId, notes) => post("/trajectories/" + id(trajectoryId) + "/suggest", notes),
  analyze: (trajectoryId, force = false) => post("/trajectories/" + id(trajectoryId) + "/jev", { force }),
  findSteps: (trajectoryId, text) => post("/trajectories/" + id(trajectoryId) + "/find", { query: text }),
  queue: (params) => request("/queue" + query(params)),
  search: (text, collection) => request("/search" + query({ q: text, collection })),
  // "" (all collections) travels as ALL_COLLECTIONS so the A / B order survives.
  explore: (collections) => request("/explore?" + collections.map((name) => "collection=" + encodeURIComponent(name || ALL_COLLECTIONS)).join("&")),
  stats: (collection) => request("/stats" + query({ collection })),
  startImport: (path, collection) => post("/imports", { path, collection: collection || null }),
  batchJev: (collection, limit) => post("/jev/batch", { collection, limit }),
  startExport: (filters, name, mode) => post("/exports", { filters, name, mode }),
  reindex: (collection) => post("/collections/reindex", { collection }),
  rewrites: (before, after) => request("/rewrites" + query({ before, after })),
  rewriteBatches: () => request("/rewrite-batches"),
  rewriteBatch: (batchId) => request("/rewrite-batches/" + id(batchId)),
  rewriteRecord: (batchId, sampleId) => request("/rewrite-batches/" + id(batchId) + "/records/" + id(sampleId)),
  createRewriteBatch: (fields) => post("/rewrite-batches", fields),
  rewriteOverview: (batchId) => request("/rewrite-batches/" + id(batchId) + "/overview"),
  rewriteChanges: (batchId, filters) => request("/rewrite-batches/" + id(batchId) + "/changes" + query(filters)),
  rewriteSettings: (batchId, settings) => post("/rewrite-batches/" + id(batchId) + "/settings", settings),
  rewriteExport: (batchId, name) => post("/rewrite-batches/" + id(batchId) + "/export", { name }),
  rewriteVerdict: (batchId, sampleId, fields) => post("/rewrite-batches/" + id(batchId) + "/records/" + id(sampleId) + "/verdicts", fields),
  rewriteDiff: (before, after) => request("/rewrite-diff" + query({ before, after })),
  correctionsUrl: (collection, kind) => API_ROOT + "/corrections/export" + query({ collection, kind }),
  job: (jobId) => request("/jobs/" + id(jobId)),
  exportUrl: (collection) => API_ROOT + "/reviews/export" + query({ collection }),
  fileUrl: (trajectoryId, relativePath) =>
    API_ROOT + "/trajectories/" + id(trajectoryId) + "/files/" + relativePath.split("/").map(encodeURIComponent).join("/"),
};

export async function pollJob(jobId, onProgress, interval = 700) {
  for (;;) {
    const job = await api.job(jobId);
    onProgress?.(job);
    if (job.status !== "running") return job;
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}
