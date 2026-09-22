import { useEffect, useState } from "react";
import { getQueue } from "../api.js";
import { displayPatientName } from "../patientNames.js";
import { EmptyState, ErrorState, LoadingState } from "./States.jsx";

export default function Patients() {
  const [patients, setPatients] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    getQueue(null, 100)
      .then(setPatients)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="panel">
      <h2>Patients</h2>
      <p className="panel-subtitle">Recent discharged patients in the risk queue</p>

      {loading && <LoadingState>Loading patients...</LoadingState>}
      <ErrorState message={error} />

      {!loading && !error && patients.length === 0 && (
        <EmptyState>No patients returned by the API.</EmptyState>
      )}

      {!loading && !error && patients.length > 0 && (
        <div className="patient-table">
          <div className="patient-row patient-row-header">
            <div>Patient</div>
            <div>Admission reason</div>
            <div>Discharge</div>
            <div>Risk</div>
          </div>
          {patients.map((patient) => (
            <div className="patient-row" key={`${patient.patient_id}-${patient.discharge_ts}`}>
              <div>
                <div className="queue-card-name">{displayPatientName(patient.patient_name)}</div>
                <div className="patient-id">{patient.patient_id.slice(0, 8)}</div>
              </div>
              <div>{patient.admission_reason}</div>
              <div>{new Date(patient.discharge_ts).toLocaleDateString()}</div>
              <div>
                <span className={`pill ${patient.risk_category === "high" ? "pill-warn" : "pill-neutral"}`}>
                  {patient.risk_category} - {patient.risk_percentile.toFixed(1)} pct
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
