import { useEffect, useState } from "react";
import { getAuditEvents, getNotifications } from "../api.js";
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

export default function Operations() {
  const [events, setEvents] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [requestId, setRequestId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  function loadEvents(filters = {}) {
    setLoading(true);
    setError(null);
    Promise.all([
      getAuditEvents({ limit: 100, requestId: filters.requestId ?? requestId }),
      getNotifications(50),
    ])
      .then(([auditResult, notificationResult]) => {
        setEvents(auditResult);
        setNotifications(notificationResult);
      })
      .catch((e) => setError(e.message))
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
  );
}
