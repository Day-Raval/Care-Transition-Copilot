import { useEffect, useState } from "react";
import { getAssessment, getPrecomputeStatus, getQueue, triggerPrecompute } from "../api.js";
import { LOW_RISK_PLAN_MESSAGE, displayCarePlanText } from "../carePlanText.js";
import { renderMarkdown } from "../markdown.js";
import { displayPatientName } from "../patientNames.js";
import { EmptyState, ErrorState, LoadingState } from "./States.jsx";

export default function CarePlans() {
  const [patients, setPatients] = useState([]);
  const [selected, setSelected] = useState(null);
  const [assessment, setAssessment] = useState(null);
  const [loadingPatients, setLoadingPatients] = useState(true);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [error, setError] = useState(null);
  const [precomputing, setPrecomputing] = useState(false);
  const [precomputeStatus, setPrecomputeStatus] = useState(null);
  const isLowRisk = assessment?.risk_category === "low";

  useEffect(() => {
    getQueue(null, 50)
      .then(setPatients)
      .catch((e) => setError(e.message))
      .finally(() => setLoadingPatients(false));

    getPrecomputeStatus()
      .then((st) => {
        if (st && st.status === "running") {
          setPrecomputing(true);
          setPrecomputeStatus(st);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let interval = null;
    if (precomputing) {
      interval = setInterval(() => {
        getPrecomputeStatus()
          .then((status) => {
            setPrecomputeStatus(status);
            if (status.status === "idle") {
              setPrecomputing(false);
            }
          })
          .catch(() => setPrecomputing(false));
      }, 1500);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [precomputing]);

  function handlePrecompute() {
    setPrecomputing(true);
    triggerPrecompute({ category: "high", limit: 10 })
      .then((res) => {
        setPrecomputeStatus(res.status);
      })
      .catch((e) => {
        setError(e.message);
        setPrecomputing(false);
      });
  }

  function selectPatient(patient) {
    setSelected(patient);
    setAssessment(null);
    setError(null);
    setLoadingPlan(true);
    getAssessment(patient.patient_id)
      .then(setAssessment)
      .catch((e) => setError(e.message))
      .finally(() => setLoadingPlan(false));
  }

  return (
    <div className="care-plan-layout">
      <div className="panel">
        <h2>Care plans</h2>
        <p className="panel-subtitle">Select a patient to view the generated follow-up plan</p>

        <div style={{ margin: "12px 0", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
          <button
            className="action-button secondary"
            style={{ fontSize: "0.82rem", padding: "6px 12px", cursor: precomputing ? "not-allowed" : "pointer" }}
            onClick={handlePrecompute}
            disabled={precomputing}
          >
            {precomputing ? "⏳ Precomputing in background..." : "⚡ Precompute High-Risk Plans"}
          </button>
          {precomputeStatus && precomputeStatus.status === "running" && (
            <span style={{ fontSize: "0.8rem", color: "#147c78", fontWeight: 500 }}>
              [{precomputeStatus.current_index}/{precomputeStatus.total || "?"}] In progress...
            </span>
          )}
          {precomputeStatus && precomputeStatus.status === "idle" && precomputeStatus.completed > 0 && (
            <span style={{ fontSize: "0.78rem", color: "#2e7d32", fontWeight: 500 }}>
              ✓ {precomputeStatus.completed} cached plans ready (<10ms load)
            </span>
          )}
        </div>

        {loadingPatients && <LoadingState>Loading patients...</LoadingState>}
        {!loadingPatients && patients.length === 0 && (
          <EmptyState>No patients available for care-plan generation.</EmptyState>
        )}
        <div className="queue-list">
          {patients.map((patient) => (
            <div
              key={`${patient.patient_id}-${patient.discharge_ts}`}
              className={`queue-card ${selected?.patient_id === patient.patient_id ? "selected" : ""}`}
              onClick={() => selectPatient(patient)}
            >
              <div className="queue-card-main">
                <div className="queue-card-name">{displayPatientName(patient.patient_name)}</div>
                <div className="queue-card-reason">{patient.admission_reason}</div>
              </div>
              <div className={`score-badge ${patient.risk_category}`}>
                <span>{patient.risk_percentile.toFixed(1)}</span>
                <small>pct</small>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel panel-gold">
        <h2>Draft follow-up plan</h2>
        <p className="panel-subtitle">
          {selected ? displayPatientName(selected.patient_name) : "No patient selected"}
        </p>

        {loadingPlan && <LoadingState>Drafting plan...</LoadingState>}
        <ErrorState message={error} />
        {!selected && !loadingPlan && (
          <EmptyState>Select a patient to generate a draft follow-up plan.</EmptyState>
        )}

        {assessment && !loadingPlan && (
          <>
            <div className="pill-row">
              <span className="pill pill-neutral">{assessment.risk_category} risk</span>
              {!isLowRisk && (
                <span className="pill pill-neutral">{assessment.risk_percentile.toFixed(1)} percentile</span>
              )}
            </div>
            <div
              className="plan-rendered"
              dangerouslySetInnerHTML={{
                __html: renderMarkdown(isLowRisk ? LOW_RISK_PLAN_MESSAGE : displayCarePlanText(assessment.draft_plan)),
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}
