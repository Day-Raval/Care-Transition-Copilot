import { useEffect, useState } from "react";
import { getAssessment, getQueue } from "../api.js";
import { displayCarePlanText } from "../carePlanText.js";
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

  useEffect(() => {
    getQueue(null, 50)
      .then((items) => {
        setPatients(items);
        if (items.length > 0) selectPatient(items[0]);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingPatients(false));
  }, []);

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

        {assessment && !loadingPlan && (
          <>
            <div className="pill-row">
              <span className="pill pill-neutral">{assessment.risk_category} risk</span>
              <span className="pill pill-neutral">{assessment.risk_percentile.toFixed(1)} percentile</span>
            </div>
            <div
              className="plan-rendered"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(displayCarePlanText(assessment.draft_plan)) }}
            />
          </>
        )}
      </div>
    </div>
  );
}
