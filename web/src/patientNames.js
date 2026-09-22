export function displayPatientName(name) {
  return String(name || "").replace(/\d+/g, "").replace(/\s+/g, " ").trim();
}
