const API_ROOT = "/api";

async function request(path, options = {}) {
  const response = await fetch(API_ROOT + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
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

export function listRuns() {
  return request("/runs");
}

export function importRun(path, label) {
  return request("/runs", {
    method: "POST",
    body: JSON.stringify({ path, label: label || null }),
  });
}

export function getRun(runId) {
  return request("/runs/" + encodeURIComponent(runId));
}

export function getMessages(runId, filters) {
  const query = new URLSearchParams();
  if (filters.roles.length) query.set("roles", filters.roles.join(","));
  if (filters.tool) query.set("tool", filters.tool);
  if (filters.search) query.set("search", filters.search);
  query.set("offset", String(filters.offset));
  query.set("limit", String(filters.limit));
  return request("/runs/" + encodeURIComponent(runId) + "/messages?" + query);
}

export function runFileUrl(runId, relativePath) {
  const encodedPath = relativePath.split("/").map(encodeURIComponent).join("/");
  return API_ROOT + "/runs/" + encodeURIComponent(runId) + "/files/" + encodedPath;
}

