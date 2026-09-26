import { BrowserRouter, Routes, Route, Link, useLocation } from "react-router-dom";
import Dashboard from "./components/Dashboard.jsx";
import ChatInterface from "./components/ChatInterface.jsx";
import Patients from "./components/Patients.jsx";
import CarePlans from "./components/CarePlans.jsx";
import { AuthProvider, useAuth } from "./AuthContext.jsx";

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
      <AuthProvider>
        <AuthenticatedApp />
      </AuthProvider>
    </BrowserRouter>
  );
}

function AuthenticatedApp() {
  const { status, error, identity, roles, isOidc, canDecide, signIn, signOut } = useAuth();

  if (status === "loading") {
    return <main className="auth-gate"><p>Checking sign-in...</p></main>;
  }
  if (status === "error" || status === "signed_out") {
    return (
      <main className="auth-gate">
        <div className="auth-panel">
          <p className="sidebar-label">CARE TRANSITION COPILOT</p>
          <h1>{status === "error" ? "Sign-in unavailable" : "Sign in"}</h1>
          {error && <p className="auth-error">{error}</p>}
          {status === "signed_out" && (
            <button className="btn approve" onClick={() => signIn()}>Continue with organization sign-in</button>
          )}
        </div>
      </main>
    );
  }

  return (
    <>
      <div className="topbar">
        <div className="topbar-title">Care Transition Copilot</div>
        <div className="topbar-session">
          <span className="topbar-user">{identity} · {roles.join(", ")}</span>
          {isOidc && <button className="btn topbar-signout" onClick={() => signOut()}>Sign out</button>}
        </div>
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
          {!canDecide && <p className="auth-role-note">Read-only access</p>}
        </div>
      </div>
    </>
  );
}
