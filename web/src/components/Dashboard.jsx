import { useEffect, useState } from "react";
import { getQueue, getAssessment } from "../api.js";
import { displayCarePlanText } from "../carePlanText.js";
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

function parseEvidenceSections(summary) {
  if (!summary) return [];
  return summary
    .split(/\n## /)
    .slice(1)
    .map((block) => {
      const lines = block.trim().split("\n").filter(Boolean);
      const title = lines[0]?.replace(/^##\s*/, "").trim();
      const excerpts = lines
        .filter((line) => line.startsWith("-"))
        .map((line) => {
          const match = line.match(/^-\s+\[([^,\]]+)(?:,[^\]]*)?\]\s*(.*)$/);
          return match
            ? { source: match[1], text: match[2] }
            : { source: title, text: line.replace(/^-\s+/, "") };
        });
      return { title, excerpts };
    })
    .filter((section) => section.title && section.excerpts.length > 0);
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
      .then((items) => {
        setQueue(items);
        if (items.length > 0) selectPatient(items[0]);
      })
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

  const evidenceSections = parseEvidenceSections(assessment?.patient_context_summary);
  let evidenceCards = [];
  if (assessment && !isLowRisk) {
    const seen = [];
    evidenceSections.forEach((section) => {
      const firstFinding = section.excerpts[0];
      if (!firstFinding) return;
      const fullText = firstFinding.text;
      if (seen.some((s) => tooSimilar(s, fullText))) return;
      seen.push(fullText);
      evidenceCards.push({ text: fullText.slice(0, 90), source: section.title });
    });
  }

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
                  <div className="evidence-list">
                    {evidenceCards.map((card, i) => (
                      <div className="evidence-card" key={i}>
                        <div className="evidence-text">{card.text}...</div>
                        <div className="evidence-source">topic: {card.source}</div>
                      </div>
                    ))}
                  </div>

                  {evidenceSections.length > 0 && (
                    <details className="cited-context">
                      <summary>View chart excerpts used by the agent</summary>
                      <div className="cited-context-list">
                        {evidenceSections.map((section) => (
                          <div className="cited-context-section" key={section.title}>
                            <div className="cited-context-title">{section.title}</div>
                            {section.excerpts.map((excerpt, i) => (
                              <div className="cited-context-item" key={i}>
                                <div>{excerpt.text}</div>
                                <div className="evidence-source">source: {excerpt.source}</div>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </details>
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

          {assessment && !loadingAssessment && (
            <>
              {isLowRisk ? (
                <p>{assessment.draft_plan}</p>
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
