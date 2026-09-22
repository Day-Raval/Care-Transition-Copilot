import { BrowserRouter, Routes, Route, Link, useLocation } from "react-router-dom";
import Dashboard from "./components/Dashboard.jsx";
import ChatInterface from "./components/ChatInterface.jsx";
import Patients from "./components/Patients.jsx";
import CarePlans from "./components/CarePlans.jsx";

const NAV_ITEMS = [
  { label: "Risk queue", path: "/", enabled: true },
  { label: "Ask a question", path: "/chat", enabled: true },
  { label: "Patients", path: "/patients", enabled: true },
  { label: "Care plans", path: "/care-plans", enabled: true },
  { label: "Fairness", path: null, enabled: false },
  { label: "Audit log", path: null, enabled: false },
];

function Sidebar() {
  const location = useLocation();
  return (
    <div className="sidebar">
      <div className="sidebar-label">WORKSPACE</div>
      <nav>
        {NAV_ITEMS.map((item) =>
          item.enabled ? (
            <Link
              key={item.label}
              to={item.path}
              className={location.pathname === item.path ? "active" : ""}
            >
              {item.label}
            </Link>
          ) : (
            <span key={item.label} className="nav-disabled" title="Not built yet">
              {item.label}
            </span>
          )
        )}
      </nav>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="topbar">
        <div className="topbar-title">Care Transition Copilot</div>
        <div className="topbar-user">Demo User</div>
      </div>
      <div className="app-shell">
        <Sidebar />
        <div className="main-content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/chat" element={<ChatInterface />} />
            <Route path="/patients" element={<Patients />} />
            <Route path="/care-plans" element={<CarePlans />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
}
