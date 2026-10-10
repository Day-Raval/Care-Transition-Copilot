import { useEffect, useState } from "react";
import { getPatientHistory, searchPatients } from "../api.js";
import { displayPatientName } from "../patientNames.js";
import { EmptyState, ErrorState, LoadingState } from "./States.jsx";

const PAGE_SIZE = 50;
const NARRATIVE_SECTIONS = new Set(["History of Present Illness", "Assessment and Plan"]);

function normalizeHistoryItem(text) {
  return text.toLowerCase().replace(/\([^)]*\)/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

function escapeRegExp(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const HISTORY_HEADINGS = [
  {
    label: "Procedures",
    phrase: "the following procedures were conducted",
    pattern: /the following procedures were conducted:?\s*/i,
  },
  {
    label: "Lab reports",
    phrase: "the following lab reports were completed",
    pattern: /the following lab reports were completed:?\s*/i,
  },
  {
    label: "Medications",
    phrase: "the patient was prescribed the following medications",
    pattern: /the patient was prescribed the following medications:?\s*/i,
  },
  {
    label: "Care plans",
    phrase: "the patient was placed on a careplan",
    pattern: /the patient was placed on a careplan:?\s*/i,
  },
];

function stripSectionPrefix(text, sectionName) {
  const escaped = sectionName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(new RegExp(`^\\s*${escaped}\\s*:\\s*`, "i"), "").trim();
}

function stripAllergyBoilerplate(text) {
  return String(text || "")
    .replace(/\bAllergies:\s*No Known Allergies\.?\s*/i, "")
    .replace(/\bMedications:\s*No Active Medications\.?\.?\s*/i, "")
    .replace(/\bNo Active Medications\.?\s*/i, "");
}

function splitListText(text) {
  const normalized = stripAllergyBoilerplate(text)
    .replace(/\r/g, "")
    .replace(/(?:^|\n)\s*[-*]\s+/g, "\n")
    .replace(/\s+-\s+(?=[A-Za-z0-9{])/g, "\n")
    .trim();
  const parts = normalized.split(/\n+|\s*;\s*/).map(cleanHistoryItem).filter(Boolean);
  return parts.length > 1 ? parts : null;
}

function sectionMode(section) {
  return NARRATIVE_SECTIONS.has(section) ? "narrative" : "list";
}

function sectionForItem(section, text) {
  if (section !== "Plan" && section !== "Assessment and Plan") return section;
  const normalized = normalizeHistoryItem(text);
  if (/\b(tablet|capsule|injection|injectable|oral|solution|syringe|mg|ml|unt ml|metoprolol|acetaminophen|insulin|heparin|enoxaparin)\b/.test(normalized)) {
    return "Medications";
  }
  if (/\b(panel|blood|serum|plasma|urine|cbc|troponin|metabolic|urinalysis|differential)\b/.test(normalized)) {
    return "Lab reports";
  }
  if (/\bcare plan\b/.test(normalized)) return "Care plans";
  if (/\b(procedure|therapy|assessment|discharge from hospital|imaging|operative|referral)\b/.test(normalized)) {
    return "Procedures";
  }
  return section;
}

function complaintItems(text) {
  const parts = splitListText(text);
  return parts || [cleanHistoryItem(text)].filter(Boolean);
}

function hpiIntroPattern(patientName) {
  if (patientName) return `${escapeRegExp(patientName).replace(/\s+/g, "\\s+")}\\s+is\\b`;
  return "[A-Z][A-Za-zÀ-ÿ'’-]+(?:\\s+[A-Z][A-Za-zÀ-ÿ'’-]+)?\\s+is\\b";
}

function splitEmbeddedChiefComplaint(line, patientName) {
  const match = line.match(/^Chief Complaint:\s*(.+)$/i);
  if (!match) return null;

  const rest = match[1].trim();
  const introMatch = rest.match(new RegExp(`\\b(${hpiIntroPattern(patientName)}.*)$`, "i"));
  const complaintText = introMatch ? rest.slice(0, introMatch.index).trim() : rest;
  const introText = introMatch ? introMatch[1].trim() : "";

  return [
    ...complaintItems(complaintText).map((item) => ({ label: "Chief Complaint", text: item })),
    ...(introText ? [{ label: "History of Present Illness", text: introText }] : []),
  ];
}

function splitLeadingComplaintFromHpi(line, patientName) {
  const match = line.match(new RegExp(`^(.+?)\\s+(${hpiIntroPattern(patientName)}.*)$`, "i"));
  if (!match) return null;

  const complaintText = match[1].trim();
  if (complaintText.split(/\s+/).length > 5) return null;

  return [
    ...complaintItems(complaintText).map((item) => ({ label: "Chief Complaint", text: item })),
    { label: "History of Present Illness", text: match[2].trim() },
  ];
}

function normalizeHistoryPatientName(text, patientName) {
  const cleaned = String(text || "").replace(/\b([A-Z][a-zA-ZÀ-ÿ'’-]+)\d+\b/g, "$1");
  if (!patientName) return cleaned;
  const firstName = patientName.split(/\s+/)[0];
  if (!firstName) return cleaned;
  return cleaned
    .replace(new RegExp(`^${firstName}\\b(?=\\s+is\\b)`, "i"), patientName)
    .replace(new RegExp(`^${patientName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s+${patientName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"), patientName);
}

function cleanHistoryItem(text) {
  const withoutTags = stripAllergyBoilerplate(text)
    .replace(/\s*\((finding|disorder|procedure|situation|record artifact)\)/gi, "");
  const cleaned = dedupeCommaClauses(withoutTags)
    .replace(/\s+/g, " ")
    .replace(/^[-*:.\s]+/, "")
    .replace(/[.;,\s]+$/, "")
    .trim();
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function dedupeCommaClauses(text) {
  if ((text.match(/,/g) || []).length < 2) return text;
  const seen = new Set();
  const clauses = text.split(/\s*,\s*/).filter((clause) => {
    const key = normalizeHistoryItem(clause);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return clauses.join(", ");
}

function historyHeadingAt(text, index) {
  const rest = text.slice(index);
  return HISTORY_HEADINGS.find((heading) => {
    const match = rest.match(heading.pattern);
    return match && match.index === 0;
  });
}

function splitByHeadings(text) {
  const lower = text.toLowerCase();
  const matches = [];
  HISTORY_HEADINGS.forEach((heading) => {
    let index = lower.indexOf(heading.phrase);
    while (index >= 0) {
      const matched = historyHeadingAt(text, index);
      if (matched) matches.push({ index, heading: matched });
      index = lower.indexOf(heading.phrase, index + 1);
    }
  });
  matches.sort((a, b) => a.index - b.index);
  if (matches.length === 0) return null;

  return matches.map((match, i) => {
    const headingText = text.slice(match.index).match(match.heading.pattern)?.[0] || "";
    const start = match.index + headingText.length;
    const end = matches[i + 1]?.index ?? text.length;
    return { label: match.heading.label, text: text.slice(start, end).trim() };
  });
}

function splitHistoryOfPresentIllness(line, label) {
  const match = line.match(/^(.*?)(?:\s+Patient has a history of\s+)(.+)$/i);
  if (!match) return null;

  const intro = cleanHistoryItem(match[1]);
  const historyItems = match[2]
    .replace(/[.;\s]+$/, "")
    .split(/\s*,\s*/)
    .map(cleanHistoryItem)
    .filter(Boolean);
  return [
    ...(intro ? [{ label, text: intro }] : []),
    ...historyItems.map((item) => ({ label: "History", text: item })),
  ];
}

function formatHistoryText(text, sectionName, patientName) {
  const section = sectionName || "Chart note";
  const cleaned = stripAllergyBoilerplate(stripSectionPrefix(normalizeHistoryPatientName(text || "", patientName), section))
    .replace(/\r/g, "")
    .trim();
  const lines = cleaned.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const groups = [];

  lines.forEach((line) => {
    const embeddedChiefComplaint = splitEmbeddedChiefComplaint(line, patientName);
    if (embeddedChiefComplaint) {
      groups.push(...embeddedChiefComplaint);
      return;
    }
    if (section === "History of Present Illness") {
      const splitComplaint = splitLeadingComplaintFromHpi(line, patientName);
      if (splitComplaint) {
        groups.push(...splitComplaint);
        return;
      }
    }
    const headingGroups = splitByHeadings(line);
    if (headingGroups) {
      groups.push(...headingGroups);
      return;
    }
    if (section === "History of Present Illness") {
      const splitHistory = splitHistoryOfPresentIllness(line, section);
      if (splitHistory) {
        groups.push(...splitHistory);
        return;
      }
    }
    if (/^[-*]\s+/.test(line)) {
      groups.push({ label: section, text: line.replace(/^[-*]\s+/, "").trim() });
      return;
    }
    const parts = splitListText(line);
    if (parts) {
      parts.forEach((part) => groups.push({ label: sectionForItem(section, part), text: part }));
    } else {
      groups.push({ label: sectionForItem(section, line), text: line });
    }
  });

  return groups;
}

function addHistoryItems(group, text) {
  const items = group.mode === "list" ? (splitListText(text) || [cleanHistoryItem(text)]) : [cleanHistoryItem(text)];
  items.forEach((item) => {
    const key = normalizeHistoryItem(item);
    if (!key || key.length < 3) return;
    const duplicateKey = findDuplicateHistoryKey(group.items, key);
    if (duplicateKey) {
      const current = group.items.get(duplicateKey);
      const preferred = preferHistoryText(current.text, item);
      group.items.delete(duplicateKey);
      group.items.set(normalizeHistoryItem(preferred), { text: preferred, count: current.count + 1 });
      return;
    }
    group.items.set(key, { text: item, count: 1 });
  });
}

function findDuplicateHistoryKey(items, key) {
  const tokens = new Set(key.split(" ").filter(Boolean));
  for (const existingKey of items.keys()) {
    if (existingKey === key || existingKey.includes(key) || key.includes(existingKey)) return existingKey;
    const existingTokens = new Set(existingKey.split(" ").filter(Boolean));
    const overlap = [...tokens].filter((token) => existingTokens.has(token)).length;
    const similarity = overlap / Math.max(1, Math.min(tokens.size, existingTokens.size));
    if (similarity >= 0.8) return existingKey;
  }
  return null;
}

function preferHistoryText(a, b) {
  const score = (value) => {
    const key = normalizeHistoryItem(value);
    const uniqueWords = new Set(key.split(" ").filter(Boolean)).size;
    const readableLength = Math.min(value.length, 260) / 260;
    const sentenceContext = /\b(patient|chief complaint|history)\b/i.test(value) ? 5 : 0;
    return uniqueWords + readableLength + sentenceContext;
  };
  return score(b) > score(a) ? b : a;
}

function buildHistoryGroups(history, patientName) {
  const bySection = new Map();

  history.forEach((entry) => {
    formatHistoryText(entry.text, entry.section_name, patientName).forEach(({ label, text }) => {
      const section = label || "Chart note";
      if (!bySection.has(section)) bySection.set(section, { label: section, mode: sectionMode(section), items: new Map() });
      addHistoryItems(bySection.get(section), text);
    });
  });

  return Array.from(bySection.values())
    .map((group) => ({
      ...group,
      items: Array.from(group.items.values()).sort((a, b) => (
        group.mode === "list" ? a.text.localeCompare(b.text) : 0
      )),
    }))
    .filter((group) => group.items.length > 0);
}

function HistoryEntry({ group }) {
  return (
    <article className="patient-history-entry">
      <div className="patient-history-meta">
        <span className="history-section-chip">{group.label}</span>
      </div>
      {group.mode === "narrative" ? (
        group.items.map((item) => <p key={normalizeHistoryItem(item.text)}>{item.text}</p>)
      ) : group.items.length > 0 && (
        <ul className="history-item-list">
          {group.items.map((item) => (
            <li key={normalizeHistoryItem(item.text)}>
              {item.text}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export default function Patients() {
  const [patients, setPatients] = useState([]);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(null);
  const [expandedEpisodeKey, setExpandedEpisodeKey] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [historyError, setHistoryError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    searchPatients({ search, category: category || null, limit: PAGE_SIZE })
      .then((result) => {
        if (cancelled) return;
        setPatients(result.items);
        setTotal(result.total);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [search, category]);

  function submitSearch(event) {
    event.preventDefault();
    setSearch(searchInput.trim());
  }

  async function loadMore() {
    setLoadingMore(true);
    setError(null);
    try {
      const result = await searchPatients({
        search,
        category: category || null,
        limit: PAGE_SIZE,
        offset: patients.length,
      });
      setPatients((current) => [...current, ...result.items]);
      setTotal(result.total);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingMore(false);
    }
  }

  async function toggleHistory(episodeKey, patientRef) {
    if (expandedEpisodeKey === episodeKey) {
      setExpandedEpisodeKey(null);
      return;
    }
    setExpandedEpisodeKey(episodeKey);
    setHistory([]);
    setHistoryHasMore(false);
    setHistoryError(null);
    setHistoryLoading(true);
    try {
      const result = await getPatientHistory(patientRef);
      setHistory(result.items);
      setHistoryHasMore(result.has_more);
    } catch (e) {
      setHistoryError(e.message);
    } finally {
      setHistoryLoading(false);
    }
  }

  async function loadMoreHistory(patientRef) {
    setHistoryLoadingMore(true);
    setHistoryError(null);
    try {
      const result = await getPatientHistory(patientRef, { offset: history.length });
      setHistory((current) => [...current, ...result.items]);
      setHistoryHasMore(result.has_more);
    } catch (e) {
      setHistoryError(e.message);
    } finally {
      setHistoryLoadingMore(false);
    }
  }

  return (
    <div className="panel">
      <h2>Patients</h2>
      <p className="panel-subtitle">Browse discharged patient episodes beyond the 20-patient risk queue.</p>

      <form className="patient-browse-controls" onSubmit={submitSearch}>
        <label className="patient-search">
          <span>Search all episodes</span>
          <input
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Name, admission reason, date, or risk"
            disabled={loadingMore}
          />
        </label>
        <label className="patient-risk-filter">
          <span>Risk category</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)} disabled={loadingMore}>
            <option value="">All categories</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <button className="btn" type="submit" disabled={loadingMore}>Search</button>
      </form>

      {loading && <LoadingState>Loading patients...</LoadingState>}
      <ErrorState message={error} />

      {!loading && !error && patients.length === 0 && (
        <EmptyState>No patient episodes match these filters.</EmptyState>
      )}

      {!loading && !error && patients.length > 0 && (
        <>
          <p className="patient-result-count">Showing {patients.length} of {total} matching episodes</p>
          <div className="patient-table">
            <div className="patient-row patient-row-header">
              <div>Patient</div>
              <div>Admission reason</div>
              <div>Discharge</div>
              <div>Risk</div>
            </div>
            {patients.map((patient) => {
              const episodeKey = `${patient.patient_ref}-${patient.discharge_ts}`;
              const patientName = displayPatientName(patient.patient_name);
              const historyGroups = buildHistoryGroups(history, patientName);
              return (
              <div className="patient-episode" key={episodeKey}>
                <div className="patient-row">
                  <div>
                    <div className="queue-card-name">{patientName}</div>
                    <div className="patient-id">Ref ...{patient.patient_ref.slice(-4)}</div>
                  </div>
                  <div>{patient.admission_reason}</div>
                  <div>{new Date(patient.discharge_ts).toLocaleDateString()}</div>
                  <div className="patient-risk-history">
                    <span className={`pill ${patient.risk_category === "high" ? "pill-warn" : "pill-neutral"}`}>
                      {patient.risk_category} - {patient.risk_percentile.toFixed(1)} pct
                    </span>
                    <button
                      className="btn patient-history-toggle"
                      type="button"
                      onClick={() => toggleHistory(episodeKey, patient.patient_ref)}
                      disabled={historyLoading}
                    >
                      {expandedEpisodeKey === episodeKey ? "Hide history" : "View history"}
                    </button>
                  </div>
                </div>
                {expandedEpisodeKey === episodeKey && (
                  <div className="patient-history">
                    <h3>Indexed chart history</h3>
                    <p className="patient-history-note">
                      Chart excerpts from records indexed by the app; this may not include the complete source medical record.
                    </p>
                    {historyLoading && <LoadingState>Loading chart history...</LoadingState>}
                    <ErrorState message={historyError} />
                    {!historyLoading && !historyError && history.length === 0 && (
                      <EmptyState>No indexed chart history is available for this patient.</EmptyState>
                    )}
                    {history.length > 0 && (
                      <>
                        <p className="patient-result-count">
                          Summarized from {history.length} indexed chart excerpts
                        </p>
                        <div className="patient-history-list">
                          {historyGroups.map((group) => (
                            <HistoryEntry group={group} key={group.label} />
                          ))}
                        </div>
                        {historyHasMore && (
                          <button
                            className="btn patient-load-more"
                            type="button"
                            onClick={() => loadMoreHistory(patient.patient_ref)}
                            disabled={historyLoadingMore}
                          >
                            {historyLoadingMore ? "Loading..." : "Load more history"}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            )})}
          </div>
          {patients.length < total && (
            <button className="btn patient-load-more" type="button" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "Loading..." : "Load more patients"}
            </button>
          )}
        </>
      )}
    </div>
  );
}
