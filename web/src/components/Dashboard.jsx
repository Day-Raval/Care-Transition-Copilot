import { useEffect, useState } from "react";
import { getQueue, getAssessment } from "../api.js";
import { LOW_RISK_PLAN_MESSAGE, displayCarePlanText } from "../carePlanText.js";
import { renderMarkdown } from "../markdown.js";
import { displayPatientName } from "../patientNames.js";

function jaccardSimilarity(a, b) {
  const wordsA = new Set(a.toLowerCase().split(/\W+/).filter((w) => w.length > 2));
  const wordsB = new Set(b.toLowerCase().split(/\W+/).filter((w) => w.length > 2));
  const intersection = new Set([...wordsA].filter((w) => wordsB.has(w)));
  const union = new Set([...wordsA, ...wordsB]);
  return union.size === 0 ? 0 : intersection.size / union.size;
}

function tooSimilar(a, b, threshold = 0.7) {
  return jaccardSimilarity(a, b) > threshold;
}

function trimRepeatedSource(source, text) {
  const prefix = `${source}:`;
  return text.toLowerCase().startsWith(prefix.toLowerCase())
    ? text.slice(prefix.length).trim().replace(/\s+/g, " ")
    : text.trim();
}

function normalizeEvidenceText(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function appendEvidenceLine(existing, line) {
  return [existing, line.replace(/^-\s+/, "").trim()].filter(Boolean).join("\n");
}

function dedupeConsecutiveLines(text) {
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  return lines
    .filter((line, index) => normalizeEvidenceText(line) !== normalizeEvidenceText(lines[index - 1] || ""))
    .join("\n");
}

function stripRepeatedHeader(text, header) {
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length === 0) return "";

  const firstLineKey = normalizeEvidenceText(lines[0]);
  const headerKey = normalizeEvidenceText(header);
  if (firstLineKey === headerKey || tooSimilar(lines[0], header, 0.9)) {
    return lines.slice(1).join("\n");
  }
  return lines.join("\n");
}

function parseEvidenceLine(line, fallbackSource) {
  const match = line.match(/^-\s+\[([^,\]]+)(?:,[^\]]*)?\]\s*(.*)$/);
  const source = match ? match[1] : fallbackSource;
  const text = trimRepeatedSource(source, match ? match[2] : line.replace(/^-\s+/, ""));
  return { source, text };
}

function parseEvidenceSections(summary) {
  if (!summary) return [];
  const sections = [];
  let current = null;
  let lastExcerpt = null;

  summary.split("\n").forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return;

    if (trimmed.startsWith("## ")) {
      current = { title: trimmed.replace(/^##\s*/, ""), excerpts: [] };
      sections.push(current);
      lastExcerpt = null;
      return;
    }

    if (!current) return;

    if (trimmed.startsWith("-") && !trimmed.startsWith("- [") && lastExcerpt) {
      lastExcerpt.text = appendEvidenceLine(lastExcerpt.text, trimmed);
      return;
    }

    if (!trimmed.startsWith("-")) {
      if (lastExcerpt && !trimmed.startsWith("(")) {
        lastExcerpt.text = appendEvidenceLine(lastExcerpt.text, trimmed);
      }
      return;
    }

    const { source, text } = parseEvidenceLine(trimmed, current.title);
    lastExcerpt = { source, text };
    current.excerpts.push(lastExcerpt);
  });

  const populatedSections = sections
    .map((section) => {
      const seen = [];
      return {
        ...section,
        excerpts: section.excerpts.filter((excerpt) => {
          excerpt.text = dedupeConsecutiveLines(excerpt.text);
          const key = normalizeEvidenceText(excerpt.text);
          if (!key || seen.some((item) => tooSimilar(item, excerpt.text))) return false;
          seen.push(excerpt.text);
          return true;
        }),
      };
    })
    .filter((section) => section.title && section.excerpts.length > 0);
  if (populatedSections.length > 0) return populatedSections;

  const fallbackExcerpts = summary
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("-"))
    .map((line) => parseEvidenceLine(line, "Chart excerpt"))
    .filter((excerpt, i, all) => (
      !all.slice(0, i).some((seen) => tooSimilar(seen.text, excerpt.text))
    ));

  return fallbackExcerpts.length > 0
    ? [{ title: "Chart excerpts returned by the agent", excerpts: fallbackExcerpts }]
    : [];
}

function cleanEvidenceItem(item) {
  return item
    .replace(/^The following\b[^:]*:\s*/i, "")
    .replace(/^The patient was prescribed the following medications:\s*/i, "")
    .replace(/^The patient was placed on a careplan:\s*/i, "")
    .replace(/^Allergies:\s*No Known Allergies\.?\.?\s*/i, "")
    .replace(/^[-:]\s*/, "")
    .trim();
}

function splitEvidenceItems(text) {
  return text
    .replace(/\)\s+-\s+/g, ")\n")
    .split(/\n|\s*;\s*|\s+\/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function uniqueItems(items, externalSeen = null) {
  const cleaned = items.map(cleanEvidenceItem).filter(Boolean);
  const seen = externalSeen || [];
  return cleaned.filter((item, index) => {
    const key = normalizeEvidenceText(item);
    const hasMoreSpecificDuplicate = cleaned.some((other, otherIndex) => {
      const otherKey = normalizeEvidenceText(other);
      return otherIndex !== index && otherKey.length > key.length && otherKey.includes(key);
    });
    if (!key || hasMoreSpecificDuplicate || seen.some((seenKey) => seenKey === key || seenKey.includes(key) || key.includes(seenKey))) {
      return false;
    }
    seen.push(key);
    return true;
  });
}

function evidenceGroupLabel(chunk) {
  if (/^The patient was prescribed\b/i.test(chunk)) return "Medications";
  if (/^The patient was placed on a careplan\b/i.test(chunk)) return "Care Plan";
  const match = chunk.match(/^The following\s+(.+?)\s+(?:were|was)\s+/i);
  if (match) {
    return match[1]
      .replace(/\bcompleted\b/i, "")
      .replace(/\bconducted\b/i, "")
      .trim()
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }
  if (/^The following medications\b/i.test(chunk)) return "Medications";
  return "";
}

function formatEvidenceText(text, seenItems = null) {
  const chunks = text
    .replace(/\s+The following\b/g, "\nThe following")
    .replace(/\s+The patient was prescribed\b/g, "\nThe patient was prescribed")
    .replace(/\s+The patient was placed on a careplan\b/g, "\nThe patient was placed on a careplan")
    .split("\n")
    .map((chunk) => chunk.trim())
    .filter(Boolean);

  const blocks = [];
  let activeListBlock = null;

  chunks.forEach((chunk) => {
    const label = evidenceGroupLabel(chunk);
    const body = cleanEvidenceItem(chunk);
    const splitParts = splitEvidenceItems(body);
    const isDelimitedList = splitParts.length > 1;
    const parts = uniqueItems(splitParts, seenItems);

    if (label) {
      activeListBlock = { label, intro: "", items: parts };
      blocks.push(activeListBlock);
      return;
    }

    if (activeListBlock) {
      if (parts.length > 0) activeListBlock.items.push(...parts);
      return;
    }

    activeListBlock = null;
    if (isDelimitedList && parts.length > 1) {
      blocks.push({ label: "", intro: "", items: parts });
      return;
    }
    blocks.push(parts.length > 1
      ? { label: "", intro: parts[0], items: parts.slice(1) }
      : { label: "", intro: body, items: [] });
  });

  return blocks;
}

function noEvidenceMessage(assessment) {
  const missing = assessment?.categories_with_no_match || [];
  if (missing.length === 0) return "No cited chart excerpts returned for this patient.";
  return `No cited chart excerpts matched: ${missing.join("; ")}.`;
}

function hasEvidenceBlock(block) {
  return block.label || block.intro || block.items.length > 0;
}

function mergeEvidenceBlocks(blocks) {
  return blocks.reduce((merged, block) => {
    const existing = block.label && !block.intro
      ? merged.find((item) => item.label === block.label && !item.intro)
      : null;
    if (existing) {
      existing.items = uniqueItems([...existing.items, ...block.items]);
      return merged;
    }
    merged.push(block);
    return merged;
  }, []);
}

function buildEvidenceDisplaySections(summary) {
  const displaySections = [];
  const seenExcerpts = [];

  parseEvidenceSections(summary).forEach((section) => {
    let displaySection = displaySections.find((existing) => tooSimilar(existing.title, section.title, 0.85));
    if (!displaySection) {
      displaySection = { title: section.title, rows: [], seenItems: [] };
      displaySections.push(displaySection);
    }

    const rowsBySource = new Map(displaySection.rows.map((row) => [row.source, row]));
    section.excerpts.forEach((excerpt) => {
      const text = stripRepeatedHeader(excerpt.text, section.title);
      if (seenExcerpts.some((seen) => tooSimilar(seen, text, 0.9))) return;
      seenExcerpts.push(text);

      const blocks = formatEvidenceText(text, displaySection.seenItems).filter(hasEvidenceBlock);
      if (blocks.length === 0) return;

      const row = rowsBySource.get(excerpt.source) || { source: excerpt.source, blocks: [] };
      row.blocks = mergeEvidenceBlocks([...row.blocks, ...blocks]);
      rowsBySource.set(excerpt.source, row);
      displaySection.rows = Array.from(rowsBySource.values());
    });
  });

  return displaySections
    .map(({ seenItems, ...section }) => section)
    .filter((section) => section.rows.length > 0);
}

export default function Dashboard() {
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(null);
  const [assessment, setAssessment] = useState(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingAssessment, setLoadingAssessment] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getQueue(null, 20)
      .then(setQueue)
      .catch((e) => setError(e.message))
      .finally(() => setLoadingQueue(false));
  }, []);

  function selectPatient(item) {
    setSelected(item);
    setAssessment(null);
    setError(null);
    setLoadingAssessment(true);
    getAssessment(item.patient_id)
      .then(setAssessment)
      .catch((e) => setError(e.message))
      .finally(() => setLoadingAssessment(false));
  }

  const isLowRisk = assessment?.risk_category === "low";
  const critiqueFlagged = assessment?.critique_notes?.toUpperCase().includes("FLAGGED");

  const evidenceSections = buildEvidenceDisplaySections(assessment?.patient_context_summary);

  return (
    <div>
      <div className="dashboard-grid">
        {/* Column 1: Risk queue */}
        <div className="panel">
          <h2>Risk queue</h2>
          <p className="panel-subtitle">Flagged after discharge, most recent first</p>
          {loadingQueue && <p className="muted">Loading…</p>}
          <div className="queue-list">
            {queue.map((item) => (
              <div
                key={`${item.patient_id}-${item.discharge_ts}`}
                className={`queue-card ${selected?.patient_id === item.patient_id ? "selected" : ""}`}
                onClick={() => selectPatient(item)}
              >
                <div className="queue-card-main">
                  <div className="queue-card-name">{displayPatientName(item.patient_name)}</div>
                  <div className="queue-card-reason">{item.admission_reason}</div>
                  <div className="queue-card-date">
                    {new Date(item.discharge_ts).toLocaleDateString()}
                  </div>
                </div>
                <div className={`score-badge ${item.risk_category}`}>
                  <span>{item.risk_percentile.toFixed(1)}</span>
                  <small>pct</small>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Column 2: Patient evidence */}
        <div className="panel">
          <h2>Patient evidence</h2>
          <p className="panel-subtitle">Why the model and agent flagged this case</p>

          {loadingAssessment && <p className="muted">Loading evidence…</p>}
          {error && <p className="error-message" style={{ padding: 0 }}>{error}</p>}
          {!selected && !loadingAssessment && <p className="muted">Select a patient to load evidence.</p>}

          {assessment && !loadingAssessment && (
            <>
              <div className="pill-row">
                <span className="pill pill-neutral">{assessment.risk_category} risk</span>
                <span className="pill pill-neutral">Cited context</span>
              </div>

              {isLowRisk ? (
                <p className="muted">Low risk — full chart review was skipped.</p>
              ) : (
                <>
                  {evidenceSections.length > 0 ? (
                    <div className="cited-context">
                      <div className="cited-context-title">Chart excerpts used by the agent</div>
                      <div className="cited-context-list">
                        {evidenceSections.map((section) => {
                          return (
                            <div className="cited-context-section" key={section.title}>
                              <div className="cited-context-title">{section.title}</div>
                              {section.rows.map((row, i) => (
                                <div className="cited-context-item" key={i}>
                                  <div className="evidence-subhead">{row.source}</div>
                                  <div className="evidence-text-block">
                                    {row.blocks.map((block, j) => (
                                      <div className="evidence-text-section" key={j}>
                                        {block.label && block.label !== row.source && (
                                          <div className="evidence-subhead">{block.label}</div>
                                        )}
                                        {block.intro && <p>{block.intro}</p>}
                                        {block.items.length > 0 && (
                                          <ul>
                                            {block.items.map((item, k) => (
                                              <li key={k}>{item}</li>
                                            ))}
                                          </ul>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              ))}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : (
                    <div className="cited-context-missing">{noEvidenceMessage(assessment)}</div>
                  )}
                </>
              )}
            </>
          )}
        </div>

        {/* Column 3: Draft follow-up plan */}
        <div className="panel panel-gold">
          <h2>Draft follow-up plan</h2>
          <p className="panel-subtitle">
            AI-generated for {assessment ? displayPatientName(assessment.patient_name) : "…"} — pending clinician action
          </p>

          {loadingAssessment && <p className="muted">Drafting plan…</p>}
          {!selected && !loadingAssessment && (
            <p className="muted">Select a patient to draft a follow-up plan.</p>
          )}

          {assessment && !loadingAssessment && (
            <>
              {isLowRisk ? (
                <p>{LOW_RISK_PLAN_MESSAGE}</p>
              ) : (
                <>
                  <div className="checklist-label">Independent review</div>
                  <div className={`pill ${critiqueFlagged ? "pill-warn" : "pill-ok"}`}>
                    {critiqueFlagged ? "Flagged — review carefully" : "Passed independent review"}
                  </div>

                  <div
                    className="plan-rendered"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(displayCarePlanText(assessment.draft_plan)) }}
                  />

                  <details>
                    <summary>View critique notes</summary>
                    <div
                      className="plan-rendered"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(assessment.critique_notes) }}
                    />
                  </details>

                  <div className="action-buttons">
                    <button className="btn approve" disabled>Approve</button>
                    <button className="btn edit" disabled>Edit</button>
                    <button className="btn reject" disabled>Reject</button>
                  </div>
                  <div className="btn-note">Not yet wired to a backend action.</div>
                </>
              )}
            </>
          )}
        </div>
      </div>

    </div>
  );
}
