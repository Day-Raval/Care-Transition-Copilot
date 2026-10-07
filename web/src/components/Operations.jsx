import { useEffect, useState } from "react";
import {
  getAuditEvents,
  getDriftReport,
  getFhirWritebacks,
  getFollowUps,
  getHealth,
  getModelInfo,
  getNotifications,
  getReminders,
} from "../api.js";
import { EmptyState, ErrorState, LoadingState } from "./States.jsx";

function prettyDate(value) {
  return value ? new Date(value).toLocaleString() : "";
}

function shortId(value) {
  return value ? `...${value.slice(-4)}` : "";
}

function EventDetails({ event }) {
  const hidden = new Set(["timestamp", "event_type", "patient_id", "patient_ref", "discharge_ts", "request_id"]);
  const details = Object.fromEntries(Object.entries(event).filter(([key]) => !hidden.has(key)));
  if (Object.keys(details).length === 0) return null;
  return <pre className="event-details">{JSON.stringify(details, null, 2)}</pre>;
}

function resultValue(result, fallback = null) {
  return result.status === "fulfilled" ? result.value : fallback;
}

function resultError(result) {
  return result.status === "rejected" ? result.reason.message : null;
}

function CheckList({ checks }) {
  if (!checks) return null;
  return (
    <div className="ops-checks">
      {Object.entries(checks).map(([name, ok]) => (
        <span className={`pill ${ok ? "pill-ok" : "pill-warn"}`} key={name}>
          {name.replaceAll("_", " ")}
        </span>
      ))}
    </div>
  );
}

export default function Operations() {
  const [events, setEvents] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [writebacks, setWritebacks] = useState([]);
  const [followUps, setFollowUps] = useState([]);
  const [reminders, setReminders] = useState([]);
  const [system, setSystem] = useState({ health: null, model: null, drift: null, errors: [] });
  const [requestId, setRequestId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  function loadEvents(filters = {}) {
    setLoading(true);
    setError(null);
    Promise.allSettled([
      getAuditEvents({ limit: 100, requestId: filters.requestId ?? requestId }),
      getNotifications(50),
      getFhirWritebacks(50),
      getFollowUps(50),
      getReminders(50),
      getHealth(),
      getModelInfo(),
      getDriftReport(),
    ])
      .then(([auditResult, notificationResult, writebackResult, followUpResult, reminderResult, healthResult, modelResult, driftResult]) => {
        setEvents(resultValue(auditResult, []));
        setNotifications(resultValue(notificationResult, []));
        setWritebacks(resultValue(writebackResult, []));
        setFollowUps(resultValue(followUpResult, []));
        setReminders(resultValue(reminderResult, []));
        setSystem({
          health: resultValue(healthResult),
          model: resultValue(modelResult),
          drift: resultValue(driftResult),
          errors: [healthResult, modelResult, driftResult].map(resultError).filter(Boolean),
        });
        const primaryError = resultError(auditResult)
          || resultError(notificationResult)
          || resultError(writebackResult)
          || resultError(followUpResult)
          || resultError(reminderResult);
        if (primaryError) setError(primaryError);
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadEvents({ requestId: "" });
  }, []);

  return (
    <div className="operations-layout">
      <div className="panel">
        <h2>Audit trail</h2>
        <p className="panel-subtitle">Recent API workflow events with patient and request trace IDs</p>

        <div className="filter-row">
          <input
            placeholder="Request ID"
            value={requestId}
            onChange={(e) => setRequestId(e.target.value)}
          />
          <button className="btn edit" onClick={() => loadEvents()}>Filter</button>
          <button
            className="btn"
            onClick={() => {
              setRequestId("");
              loadEvents({ requestId: "" });
            }}
          >
            Clear
          </button>
        </div>

        {loading && <LoadingState>Loading operations...</LoadingState>}
        <ErrorState message={error} />
        {!loading && !error && events.length === 0 && <EmptyState>No audit events found.</EmptyState>}

        {!loading && !error && events.length > 0 && (
          <div className="event-list">
            {events.map((event, index) => (
              <div className="event-row" key={`${event.timestamp}-${event.event_type}-${index}`}>
                <div>
                  <div className="event-title">{event.event_type}</div>
                  <div className="event-meta">
                    {prettyDate(event.timestamp)}
                    {event.patient_ref && ` | patient ${shortId(event.patient_ref)}`}
                    {event.request_id && ` | request ${shortId(event.request_id)}`}
                  </div>
                </div>
                <EventDetails event={event} />
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="ops-stack">
        <div className="panel">
          <h2>System visibility</h2>
          <p className="panel-subtitle">Runtime checks, model metadata, and drift status</p>

          {loading && <LoadingState>Loading status...</LoadingState>}
          {!loading && system.health && (
            <>
              <div className={`decision-badge ${system.health.status === "ok" ? "approved" : "rejected"}`}>
                API {system.health.status}
              </div>
              <CheckList checks={system.health.dependencies?.checks} />
              <div className="event-meta">Kafka: {system.health.kafka_consumer?.status || "unknown"}</div>
            </>
          )}
          {!loading && system.model && (
            <div className="event-row">
              <div className="event-title">{system.model.model_type} | {system.model.model_run_id}</div>
              <div className="event-meta">
                {system.model.training_events} training events | {system.model.fairness_status}
              </div>
            </div>
          )}
          {!loading && system.drift && (
            <div className="event-row">
              <div className="event-title">Drift: {system.drift.status}</div>
              <div className="event-meta">{system.drift.n_recent_predictions} recent predictions</div>
            </div>
          )}
          {!loading && system.errors.map((message) => (
            <p className="muted" key={message}>{message}</p>
          ))}
        </div>

        <div className="panel">
          <h2>FHIR write-backs</h2>
          <p className="panel-subtitle">Approved-plan CarePlan resources recorded by the local stub</p>

          {!loading && !error && writebacks.length === 0 && <EmptyState>No write-backs recorded yet.</EmptyState>}
          {!loading && !error && writebacks.length > 0 && (
            <div className="event-list">
              {writebacks.map((writeback, index) => (
                <div className="event-row" key={`${writeback.timestamp}-${index}`}>
                  <div className="event-title">{writeback.resource_type || "FHIR"}: {writeback.status}</div>
                  <div className="event-meta">
                    {prettyDate(writeback.timestamp)}
                    {writeback.patient_ref && ` | patient ${shortId(writeback.patient_ref)}`}
                  </div>
                  <EventDetails event={writeback} />
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Follow-ups</h2>
          <p className="panel-subtitle">Latest care-transition follow-up statuses</p>

          {!loading && !error && followUps.length === 0 && <EmptyState>No follow-up statuses recorded yet.</EmptyState>}
          {!loading && !error && followUps.length > 0 && (
            <div className="event-list">
              {followUps.map((followUp, index) => (
                <div className="event-row" key={`${followUp.timestamp}-${index}`}>
                  <div className="event-title">{followUp.status}</div>
                  <div className="event-meta">
                    {prettyDate(followUp.timestamp)}
                    {followUp.patient_ref && ` | patient ${shortId(followUp.patient_ref)}`}
                    {followUp.actor && ` | ${followUp.actor}`}
                  </div>
                  {followUp.note && <p className="muted">{followUp.note}</p>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Reminders</h2>
          <p className="panel-subtitle">Scheduled notification reminders for follow-up work</p>

          {!loading && !error && reminders.length === 0 && <EmptyState>No reminders scheduled yet.</EmptyState>}
          {!loading && !error && reminders.length > 0 && (
            <div className="event-list">
              {reminders.map((reminder, index) => (
                <div className="event-row" key={`${reminder.timestamp}-${index}`}>
                  <div className="event-title">{reminder.status}: {prettyDate(reminder.remind_at)}</div>
                  <div className="event-meta">
                    {reminder.patient_ref && `patient ${shortId(reminder.patient_ref)}`}
                    {reminder.actor && ` | ${reminder.actor}`}
                  </div>
                  {reminder.message && <p className="muted">{reminder.message}</p>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Notifications</h2>
          <p className="panel-subtitle">Approved-plan handoffs recorded after clinician decisions</p>

          {!loading && !error && notifications.length === 0 && <EmptyState>No notifications recorded yet.</EmptyState>}
          {!loading && !error && notifications.length > 0 && (
            <div className="event-list">
              {notifications.map((notification, index) => (
                <div className="event-row" key={`${notification.timestamp}-${index}`}>
                  <div className="event-title">
                    {notification.channel || "notification"}: {notification.status || "recorded"}
                  </div>
                  <div className="event-meta">
                    {prettyDate(notification.timestamp)}
                    {notification.patient_ref && ` | patient ${shortId(notification.patient_ref)}`}
                  </div>
                  {notification.message && <p className="muted">{notification.message}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
