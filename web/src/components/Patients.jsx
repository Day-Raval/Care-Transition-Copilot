import { useEffect, useState } from "react";
import { getPatientHistory, searchPatients } from "../api.js";
import { displayPatientName } from "../patientNames.js";
import { EmptyState, ErrorState, LoadingState } from "./States.jsx";

const PAGE_SIZE = 50;

function normalizeHistoryItem(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function stripSectionPrefix(text, sectionName) {
  const escaped = sectionName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(new RegExp(`^\\s*${escaped}\\s*:\\s*`, "i"), "").trim();
}

function splitDelimitedText(text) {
  const parts = text.split(/\s*;\s*/).map((part) => part.trim()).filter(Boolean);
  return parts.length > 2 ? parts : null;
}

function normalizeHistoryPatientName(text, patientName) {
  if (!patientName) return text;
  return text.replace(/\b[A-Z][a-z]+[0-9]{2,}\b/g, patientName);
}

function formatHistoryText(text, sectionName, patientName) {
  const cleaned = stripSectionPrefix(normalizeHistoryPatientName(text || "", patientName), sectionName || "")
    .replace(/\r/g, "")
    .trim();
  const lines = cleaned.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const intro = [];
  const rawItems = [];

  lines.forEach((line) => {
    if (/^[-*]\s+/.test(line)) {
      rawItems.push(line.replace(/^[-*]\s+/, "").trim());
      return;
    }
    const parts = splitDelimitedText(line);
    if (parts) {
      rawItems.push(...parts);
    } else {
      intro.push(line);
    }
  });

  const counts = new Map();
  rawItems.forEach((item) => {
    const key = normalizeHistoryItem(item);
    if (!key) return;
    const current = counts.get(key);
    counts.set(key, { text: current?.text || item, count: (current?.count || 0) + 1 });
  });

  return { intro, items: Array.from(counts.values()) };
}

function HistoryEntry({ entry, index, patientName }) {
  const formatted = formatHistoryText(entry.text, entry.section_name, patientName);
  return (
    <article className="patient-history-entry" key={`${entry.discharge_ts}-${entry.section_name}-${index}`}>
      <div className="patient-history-meta">
        <span className="history-section-chip">{entry.section_name || "Chart note"}</span>
      </div>
      {formatted.intro.map((line, lineIndex) => (
        <p key={lineIndex}>{line}</p>
      ))}
      {formatted.items.length > 0 && (
        <ul className="history-item-list">
          {formatted.items.map((item) => (
            <li key={normalizeHistoryItem(item.text)}>
              {item.text}
              {item.count > 1 && <span className="history-repeat"> x{item.count}</span>}
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
                        <p className="patient-result-count">Showing {history.length} chart excerpts</p>
                        <div className="patient-history-list">
                          {history.map((entry, index) => (
                            <HistoryEntry
                              entry={entry}
                              index={index}
                              patientName={patientName}
                              key={`${entry.discharge_ts}-${entry.section_name}-${index}`}
                            />
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
