// Minimal markdown renderer for the specific subset the reasoning and
// critique agents actually produce: **bold**, "- " bullet lists,
// "1. " numbered lists, and paragraph breaks. Not a general-purpose
// markdown parser -- deliberately small and specific to what's actually
// needed, tested against real LLM output from this project rather than
// invented sample text.
//
// Safe to render as raw HTML (see Dashboard.jsx's use of
// dangerouslySetInnerHTML) because this only ever processes OUR OWN
// trusted LLM output, not arbitrary user-supplied content.
export function renderMarkdown(text) {
  if (!text) return "";
  const lines = text.split("\n");
  let html = "";
  let inList = null; // 'ul' | 'ol' | null

  function closeList() {
    if (inList) {
      html += `</${inList}>`;
      inList = null;
    }
  }

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      closeList();
      continue;
    }

    const bold = line
      .replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

    const bulletMatch = bold.match(/^-\s+(.*)/);
    const numberedMatch = bold.match(/^\d+\.\s+(.*)/);

    if (bulletMatch) {
      if (inList !== "ul") {
        closeList();
        html += "<ul>";
        inList = "ul";
      }
      html += `<li>${bulletMatch[1]}</li>`;
    } else if (numberedMatch) {
      if (inList !== "ol") {
        closeList();
        html += "<ol>";
        inList = "ol";
      }
      html += `<li>${numberedMatch[1]}</li>`;
    } else {
      closeList();
      html += `<p>${bold}</p>`;
    }
  }
  closeList();
  return html;
}