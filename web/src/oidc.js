import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const configuredAuthMode = (import.meta.env.VITE_AUTH_MODE || "api_key").toLowerCase();
export const AUTH_MODE = configuredAuthMode === "oidc" ? "oidc" : "api_key";
export const IS_OIDC_MODE = AUTH_MODE === "oidc";
export const OIDC_ROLES_CLAIM = import.meta.env.VITE_OIDC_ROLES_CLAIM || "roles";

const authority = import.meta.env.VITE_OIDC_AUTHORITY || "";
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || "";
const scope = import.meta.env.VITE_OIDC_SCOPE || "";

export const oidcConfigurationError = !["api_key", "oidc"].includes(configuredAuthMode)
  ? "VITE_AUTH_MODE must be api_key or oidc."
  : IS_OIDC_MODE && (!authority || !clientId || !scope)
    ? "OIDC mode needs VITE_OIDC_AUTHORITY, VITE_OIDC_CLIENT_ID, and VITE_OIDC_SCOPE."
    : null;

export const oidcManager = IS_OIDC_MODE && !oidcConfigurationError
  ? new UserManager({
      authority,
      client_id: clientId,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: "code",
      scope,
      automaticSilentRenew: true,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    })
  : null;

export async function getOidcAccessToken() {
  const user = await oidcManager?.getUser();
  if (!user || user.expired || !user.access_token) {
    throw new Error("Your sign-in session has expired. Sign in again to continue.");
  }
  return user.access_token;
}