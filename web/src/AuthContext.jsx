import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  IS_OIDC_MODE,
  OIDC_ROLES_CLAIM,
  oidcConfigurationError,
  oidcManager,
} from "./oidc.js";

const AuthContext = createContext(null);

function getRoles(user) {
  const claim = user?.profile?.[OIDC_ROLES_CLAIM];
  if (Array.isArray(claim)) return claim.filter((role) => typeof role === "string");
  return typeof claim === "string" ? claim.split(/\s+/).filter(Boolean) : [];
}

export function AuthProvider({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState(IS_OIDC_MODE ? "loading" : "ready");
  const [error, setError] = useState(oidcConfigurationError);

  useEffect(() => {
    if (!IS_OIDC_MODE) {
      setStatus("ready");
      return undefined;
    }
    if (oidcConfigurationError || !oidcManager) {
      setStatus("error");
      return undefined;
    }

    let active = true;
    const onUserLoaded = (nextUser) => {
      if (active) {
        setUser(nextUser);
        setStatus("ready");
      }
    };
    const onUserUnloaded = () => {
      if (active) {
        setUser(null);
        setStatus("signed_out");
      }
    };
    const onTokenExpired = () => {
      if (active) {
        setUser(null);
        setStatus("signed_out");
      }
    };

    oidcManager.events.addUserLoaded(onUserLoaded);
    oidcManager.events.addUserUnloaded(onUserUnloaded);
    oidcManager.events.addAccessTokenExpired(onTokenExpired);

    async function initialize() {
      try {
        if (location.pathname === "/auth/callback") {
          const callbackUser = await oidcManager.signinRedirectCallback();
          if (!active) return;
          setUser(callbackUser);
          setStatus("ready");
          navigate(callbackUser.state?.returnTo || "/", { replace: true });
          return;
        }

        const storedUser = await oidcManager.getUser();
        if (!active) return;
        if (storedUser && !storedUser.expired) {
          setUser(storedUser);
          setStatus("ready");
        } else {
          setUser(null);
          setStatus("signed_out");
        }
      } catch {
        if (active) {
          setError("Sign-in could not be completed. Check the identity provider configuration and try again.");
          setStatus("error");
        }
      }
    }

    initialize();
    return () => {
      active = false;
      oidcManager.events.removeUserLoaded(onUserLoaded);
      oidcManager.events.removeUserUnloaded(onUserUnloaded);
      oidcManager.events.removeAccessTokenExpired(onTokenExpired);
    };
  }, [location.pathname, navigate]);

  const value = useMemo(() => {
    const roles = IS_OIDC_MODE ? getRoles(user) : ["admin"];
    const profile = user?.profile || {};
    return {
      status,
      error,
      user,
      roles,
      isOidc: IS_OIDC_MODE,
      canDecide: roles.includes("clinician") || roles.includes("admin"),
      identity: IS_OIDC_MODE
        ? profile.preferred_username || profile.email || profile.sub || "Clinician"
        : import.meta.env.VITE_CLINICIAN_ID || "demo_clinician",
      async signIn() {
        setError(null);
        await oidcManager.signinRedirect({ state: { returnTo: location.pathname } });
      },
      async signOut() {
        await oidcManager.signoutRedirect();
      },
    };
  }, [error, location.pathname, status, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}