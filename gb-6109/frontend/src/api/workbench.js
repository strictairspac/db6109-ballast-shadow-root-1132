import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  roots: id => request(`/runs/${id}/roots`),
  visibleRoots: (runId, recipe) => {
    const params = new URLSearchParams();
    if (runId) params.set("run_id", runId);
    if (recipe) params.set("recipe", recipe);
    const suffix = params.size ? `?${params}` : "";
    return request(`/roots${suffix}`);
  },
  quarantine: id => request(`/runs/${id}/quarantine`),
  repairTickets: id => request(`/runs/${id}/repair`),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),
};
