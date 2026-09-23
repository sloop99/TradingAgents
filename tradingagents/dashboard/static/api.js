// Thin wrappers over the dashboard's JSON endpoints. Every failure becomes an ApiError
// with a message a person can act on, plus the HTTP status (0 when the server is unreachable).

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson(url, action, options = {}) {
  let response;
  try {
    response = await fetch(url, { cache: "no-store", ...options });
  } catch {
    throw new ApiError(`${action} failed: the dashboard server isn't responding. Is it still running?`, 0);
  }
  if (!response.ok) throw new ApiError(`${action} failed (HTTP ${response.status}).`, response.status);
  return response.json();
}

export const getRuns = () => getJson("/api/runs", "Loading the research archive");
export const refreshRuns = () => getJson("/api/refresh", "Refreshing the archive", { method: "POST" });
export const getSectors = () => getJson("/api/sectors", "Loading sector observations");
export const getReport = (runId, section) => getJson(
  `/api/report?id=${encodeURIComponent(runId)}&section=${encodeURIComponent(section)}`,
  "Loading this section",
);
