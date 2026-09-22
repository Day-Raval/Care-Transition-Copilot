const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function getQueue(category = null, limit = 50) {
  const params = new URLSearchParams({ limit });
  if (category) params.set("category", category);
  return request(`/patients?${params}`);
}

export function getAssessment(patientId) {
  return request(`/patients/${patientId}/assessment`);
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