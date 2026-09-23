const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS || 30000);
const ASSESSMENT_TIMEOUT_MS = Number(import.meta.env.VITE_ASSESSMENT_TIMEOUT_MS || 120000);
const API_KEY = import.meta.env.VITE_API_KEY || "";

async function request(path, options = {}) {
  const { timeoutMs = REQUEST_TIMEOUT_MS, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const headers = {
    "Content-Type": "application/json",
    ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
    ...(fetchOptions.headers || {}),
  };

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      headers,
      signal: controller.signal,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed: ${res.status}`);
    }
    return res.json();
  } catch (e) {
    if (e.name === "AbortError") {
      throw new Error("Request timed out. The API may still be generating a response.");
    }
    throw e;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function getQueue(category = null, limit = 50) {
  const params = new URLSearchParams({ limit });
  if (category) params.set("category", category);
  return request(`/patients?${params}`);
}

export function getAssessment(patientId, dischargeTs = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientId}/assessment${params}`, { timeoutMs: ASSESSMENT_TIMEOUT_MS });
}

export function getDecision(patientId, dischargeTs = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientId}/decision${params}`);
}

export function saveDecision(patientId, dischargeTs, decision) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientId}/decision${params}`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}

export function getSavedCarePlans(limit = 50) {
  return request(`/care-plans?${new URLSearchParams({ limit })}`);
}

export function sendChatMessage(question) {
  return request("/chat", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

export function getModelInfo() {
  return request("/model-info");
}
