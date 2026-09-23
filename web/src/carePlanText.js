export const LOW_RISK_PLAN_MESSAGE =
  "Low readmission risk. Full chart review and AI care-plan drafting were not triggered for this patient. Continue standard discharge instructions, medication reconciliation, and routine follow-up per the care team's protocol.";

export function displayCarePlanText(text) {
  return String(text || "")
    .replace(/DOCUMENTATION GAPS/gi, "ADDITIONAL REVIEW NOTES")
    .replace(/Documentation gaps/gi, "Additional review notes")
    .replace(/CHART INFORMATION NOT FOUND/gi, "ADDITIONAL REVIEW NOTES")
    .replace(/Chart information not found/gi, "Additional review notes")
    .replace(/^.*no relevant documentation found.*$/gim, "");
}
