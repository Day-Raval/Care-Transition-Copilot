export function displayCarePlanText(text) {
  return String(text || "")
    .replace(/DOCUMENTATION GAPS/gi, "ADDITIONAL REVIEW NOTES")
    .replace(/Documentation gaps/gi, "Additional review notes")
    .replace(/CHART INFORMATION NOT FOUND/gi, "ADDITIONAL REVIEW NOTES")
    .replace(/Chart information not found/gi, "Additional review notes")
    .replace(/^.*no relevant documentation found.*$/gim, "");
}
