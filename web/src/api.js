import { AUTH_MODE, getOidcAccessToken } from "./oidc.js";

const API_BASE = import.meta.env.DEV ? "/api" : (import.meta.env.VITE_API_BASE_URL || "/api");
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS || 30000);
const ASSESSMENT_TIMEOUT_MS = Number(import.meta.env.VITE_ASSESSMENT_TIMEOUT_MS || 120000);
const CHAT_TIMEOUT_MS = Number(import.meta.env.VITE_CHAT_TIMEOUT_MS || 120000);
const HISTORY_TIMEOUT_MS = Number(import.meta.env.VITE_HISTORY_TIMEOUT_MS || 120000);
const API_KEY = import.meta.env.VITE_API_KEY || "";
const CLINICIAN_ID = import.meta.env.VITE_CLINICIAN_ID || "demo_clinician";

function newRequestId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function errorWithRequestId(message, requestId) {
  return new Error(`${message} (Request ID: ${requestId})`);
}

async function request(path, options = {}) {
  const { timeoutMs = REQUEST_TIMEOUT_MS, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const requestId = newRequestId();

  try {
    const authHeaders = AUTH_MODE === "oidc"
      ? { Authorization: `Bearer ${await getOidcAccessToken()}` }
      : {
          "X-Clinician-ID": CLINICIAN_ID,
          ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
        };
    const headers = {
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
      ...authHeaders,
      ...(fetchOptions.headers || {}),
    };
    const res = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      headers,
      signal: controller.signal,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const responseRequestId = res.headers.get("x-request-id") || body.request_id || headers["X-Request-ID"];
      throw errorWithRequestId(body.detail || `Request failed: ${res.status}`, responseRequestId);
    }
    return res.json();
  } catch (e) {
    if (e.name === "AbortError") {
      throw errorWithRequestId("Request timed out. The API may still be working; try again in a moment.", requestId);
    }
    if (e instanceof TypeError && e.message === "Failed to fetch") {
      throw errorWithRequestId(`Could not reach the API through ${API_BASE}. Start FastAPI with uvicorn and Vite with npm run dev.`, requestId);
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

export function searchPatients({ search = "", category = null, limit = 50, offset = 0 } = {}) {
  return request("/patients/search", {
    method: "POST",
    body: JSON.stringify({ search, category, limit, offset }),
  });
}

export function getPatientHistory(patientRef, { limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit, offset });
  return request(`/patients/${patientPath(patientRef)}/history?${params}`, { timeoutMs: HISTORY_TIMEOUT_MS });
}

function patientPath(patientRef) {
  return encodeURIComponent(patientRef);
}

export function getAssessment(patientRef, dischargeTs = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientPath(patientRef)}/assessment${params}`, { timeoutMs: ASSESSMENT_TIMEOUT_MS });
}

export function getDecision(patientRef, dischargeTs = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientPath(patientRef)}/decision${params}`);
}

export function saveDecision(patientRef, dischargeTs, decision, draftPlan = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  const body = draftPlan ? { decision, draft_plan: draftPlan } : { decision };
  return request(`/patients/${patientPath(patientRef)}/decision${params}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getReport(patientRef, dischargeTs = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientPath(patientRef)}/report${params}`);
}

export function getFollowUp(patientRef, dischargeTs = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientPath(patientRef)}/follow-up${params}`);
}

export function saveFollowUpStatus(patientRef, dischargeTs, status, note = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientPath(patientRef)}/follow-up${params}`, {
    method: "POST",
    body: JSON.stringify({ status, note }),
  });
}

export function scheduleReminder(patientRef, dischargeTs, remindAt, message = null) {
  const params = dischargeTs ? `?${new URLSearchParams({ discharge_ts: dischargeTs })}` : "";
  return request(`/patients/${patientPath(patientRef)}/reminders${params}`, {
    method: "POST",
    body: JSON.stringify({ remind_at: remindAt, message }),
  });
}

export function getSavedCarePlans(limit = 50) {
  return request(`/care-plans?${new URLSearchParams({ limit })}`);
}

export function getNotifications(limit = 50) {
  return request(`/notifications?${new URLSearchParams({ limit })}`);
}

export function getFhirWritebacks(limit = 50) {
  return request(`/fhir-writebacks?${new URLSearchParams({ limit })}`);
}

export function getFollowUps(limit = 50) {
  return request(`/follow-ups?${new URLSearchParams({ limit })}`);
}

export function getReminders(limit = 50) {
  return request(`/reminders?${new URLSearchParams({ limit })}`);
}

export function getAuditEvents({ limit = 100, requestId = "" } = {}) {
  const params = new URLSearchParams({ limit });
  if (requestId) params.set("request_id", requestId);
  return request(`/audit-events?${params}`);
}

export function sendChatMessage(question, patientName = null) {
  return request("/chat", {
    method: "POST",
    timeoutMs: CHAT_TIMEOUT_MS,
    body: JSON.stringify({ question, patient_name: patientName }),
  });
}

export function getModelInfo() {
  return request("/model-info");
}

export function getHealth() {
  return request("/health");
}

export function getDriftReport() {
  return request("/drift-report");
}

export function triggerPrecompute({ category = "high", limit = 10, force = false } = {}) {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (limit) params.set("limit", String(limit));
  if (force) params.set("force", "true");
  return request(`/tasks/precompute-assessments?${params}`, {
    method: "POST",
  });
}

export function getPrecomputeStatus() {
  return request("/tasks/precompute-assessments/status");
}
