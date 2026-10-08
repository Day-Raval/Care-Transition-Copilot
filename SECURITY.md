# Security

This document explains the security controls currently implemented in Care Transition Copilot, why they exist, how to configure them, and which risks remain the responsibility of an operator. The application is a synthetic-data research/demo project; it is not validated for clinical use and is not represented as HIPAA-compliant or production-ready for protected health information (PHI).

## Security goals

The controls are intended to:

- Restrict API access to authenticated callers.
- Require appropriate roles for sensitive clinician and data-science actions.
- Attribute decisions to a verified identity in OIDC mode rather than a caller-provided name.
- Keep model predictions and AI-generated plans advisory, reviewable, and auditable.
- Correlate API errors and events with request IDs without returning internal exception details to clients.
- Treat retrieved chart text and tool outputs as untrusted evidence, not executable instructions.
- Limit abuse of expensive AI, search, assessment, and intake paths.
- Keep local development convenient while making its weaker security properties explicit.

## Trust boundaries

The application has several distinct trust boundaries:

1. **Browser to API:** the React app sends either a bearer access token in OIDC mode or a shared API key in local demo mode.
2. **API to identity provider:** the API obtains the signing key from the configured JWKS endpoint and validates access-token claims.
3. **API to model service:** in OIDC mode, `/predict` uses the service API key rather than an end-user token.
4. **Application to storage, notification, and model providers:** patient/episode data, generated plans, audit events, notification messages, and LLM requests may cross to configured databases, vector stores, SMS providers, or external model providers. Operators must assess and approve those services before using sensitive data.

The browser is an untrusted client. Frontend checks improve usability but do not enforce security; the API is authoritative.

## Authentication modes

### OIDC mode (user access)

Set `AUTH_MODE=oidc` for the API and `VITE_AUTH_MODE=oidc` for the browser. The SPA uses the OpenID Connect authorization-code flow with PKCE through `oidc-client-ts`. Configure a public SPA client with no client secret and register the exact browser redirect URI ending in `/auth/callback` and the post-logout URI.

The API accepts `Authorization: Bearer <access-token>` on protected user routes. It validates RS256 signatures using `OIDC_JWKS_URL` and checks issuer, audience, expiry, issued-at time, and subject. The roles claim is read from `OIDC_ROLES_CLAIM` (default `roles`). Role values may be a list or a whitespace-separated string.

Configure these API values in your .env file:

```dotenv
AUTH_MODE=oidc
OIDC_ISSUER=https://identity.example.com/tenant/v2.0
OIDC_AUDIENCE=api://care-transition-api
OIDC_JWKS_URL=https://identity.example.com/tenant/discovery/v2.0/keys
OIDC_ROLES_CLAIM=roles
API_KEY=<random-service-key>
```

Configure the matching browser values:

```dotenv
VITE_AUTH_MODE=oidc
VITE_API_BASE_URL=https://api.example.com
VITE_OIDC_AUTHORITY=https://identity.example.com/tenant/v2.0
VITE_OIDC_CLIENT_ID=<spa-client-id>
VITE_OIDC_SCOPE=openid profile api://care-transition-api/access_as_user
VITE_OIDC_ROLES_CLAIM=roles
CORS_ALLOWED_ORIGINS=https://app.example.com
```

The identity provider must issue an RS256 **access token** for the configured API audience, with `iss`, `aud`, `exp`, `iat`, `sub`, and the configured role claim. Assign only the roles needed by each user. Ensure the SPA receives the role claim in its profile if role-aware UI controls should be displayed; API authorization independently checks the access token's roles.

In OIDC mode, `/predict` is a service-to-service route and requires `X-API-Key` matching the API's `API_KEY`. Do not send this service key to the browser. The `API_KEY` must be shared only with trusted backend callers that need `/predict`.

### API-key mode (local demo only)

`AUTH_MODE=api_key` is the default for local development. It requires `X-API-Key` for all routes except `/health` and CORS preflight. It is not user authentication or RBAC: every holder of the shared key is treated as an administrator, and the optional `X-Clinician-ID` is caller-controlled. The `VITE_API_KEY` value is compiled into browser assets and is therefore visible to users. Use synthetic data only; never treat this mode as suitable for public or production deployment.

Generate a high-entropy demo key, keep `.env` out of source control, and do not reuse credentials across environments. A key embedded in a built SPA must be considered public regardless of obfuscation.

## Authorization policy

OIDC requests are authorized by role and route in `src/api/security.py`. Unmapped routes are denied by default. The current policy is:

| Action | Required role |
| --- | --- |
| Read patient queue, assessment, decision, care-plan list, chat, or report | `care_coordinator`, `clinician`, or `admin` |
| Approve or reject a patient care plan | `clinician` or `admin` |
| Read drift report | `data_scientist` or `admin` |
| Read model information | `care_coordinator`, `clinician`, `data_scientist`, or `admin` |
| Call `/predict` in OIDC mode | Service API key; not a user role |
| Call `/health` or send CORS preflight | Public |

The API derives the decision actor from the validated OIDC `sub` claim. The decision request schema does not accept an actor field. This prevents a caller from changing decision attribution with a request body or `X-Clinician-ID` header in OIDC mode. The browser hides decision controls from users without a clinician/admin role, but this is only a usability measure; the API check prevents unauthorized writes.

Review the route policy whenever adding or changing an API endpoint. Add tests for both allowed and denied roles and for sensitive writes.

## Other implemented safeguards

- **Request tracing:** the API accepts or creates an `X-Request-ID`, returns it in the response, and includes it in safe error responses. Do not put secrets, tokens, or patient details in a request ID.
- **Error handling:** unexpected errors are logged server-side while clients receive a generic response and request ID. Server logs still require access controls and retention limits.
- **CORS:** browser origins are restricted by `CORS_ALLOWED_ORIGINS`; the default permits local Vite origins. CORS is a browser policy, not authentication, and does not block non-browser clients.
- **Request size limits:** the API rejects oversized `POST`, `PUT`, and `PATCH` requests before route handling. Configure `MAX_REQUEST_BYTES` per environment.
- **Route throttling:** the API applies lightweight in-memory per-principal throttles to expensive routes: `/chat`, `/patients/{patient_ref}/assessment`, `/patients/search`, `/intake/hl7-adt`, and `/intake/discharge-event`. Configure `RATE_LIMIT_CHAT_PER_MINUTE`, `RATE_LIMIT_ASSESSMENT_PER_MINUTE`, `RATE_LIMIT_WRITE_PER_MINUTE`, and `RATE_LIMIT_WINDOW_SECONDS`. This is suitable for the MVP and single-process demos; use an API gateway, WAF, or Redis-backed limiter for multi-instance deployments.
- **Input bounds:** chat and intake request schemas cap user question, patient name, HL7 message, timestamp, encounter, and admission-reason lengths. These limits reduce accidental large payloads and basic abuse, but they do not replace authorization, logging, or content review.
- **Human review:** care plans remain drafts until a clinician approves them. Reports are generated only after an approved decision; rejected plans return an edit-required status. This workflow does not make the model or generated recommendations clinically validated.
- **Patient-scoped retrieval:** note retrieval is filtered to the requested patient before semantic ranking. Keep this invariant covered when modifying retrieval or data access paths.
- **RAG and prompt-injection guardrails:** retrieved chart excerpts are wrapped in `BEGIN_CHART_CONTEXT` / `END_CHART_CONTEXT`, and tool outputs passed back to the chat model are wrapped in `BEGIN_TOOL_RESULT` / `END_TOOL_RESULT`. The chat system prompt instructs the model to treat those blocks as untrusted clinical evidence only and to ignore embedded requests to reveal prompts, credentials, patient IDs, tool schemas, hidden policies, logs, or to change instructions.
- **Credential and internals refusal:** chat instructions explicitly prohibit revealing API keys, credentials, hidden prompts, environment variables, tool schemas, raw patient identifiers, logs, stack traces, and internal file paths. Output redaction still runs on chat responses and tool-call logs before returning them to the browser.
- **Audit and persistence:** decision, care-plan, and audit records can use local SQLite/JSONL or the configured SQL database. Local files are demo storage, not durable or managed audit storage. Audit records may contain patient identifiers and other sensitive context; protect and retain them accordingly.
- **Notification handoff:** approved decisions create a notification record. The local portal stub does not contact an external service; configured Twilio SMS sends the message to the supplied recipient. Keep Twilio credentials server-side, restrict provider access, and treat message content and delivery metadata as sensitive. Notification failure does not bypass clinician approval or fail the decision write.
- **Model caveats:** the risk output is a relative score, not a calibrated probability. The current fairness audit is inconclusive. API responses include a research/not-clinically-validated disclaimer.

## Data and secrets

- Use synthetic Synthea data for local development and demonstrations.
- Do not commit `.env`, access tokens, API keys, database credentials, or real patient data. Rotate any credential that is accidentally exposed.
- Do not place secrets in `VITE_*` variables. Vite exposes these values to the browser bundle.
- Never place Twilio credentials in `VITE_*` variables or frontend code. They are server-side secrets and must not be sent to the browser.
- Use distinct secrets per environment and prefer a managed secret store such as GitHub Actions secrets, AWS Secrets Manager, Azure Key Vault, GCP Secret Manager, Doppler, or 1Password for deployed environments.
- Run secret scanning in CI. The repository CI includes a Gitleaks scan to catch accidentally committed tokens and credentials; treat a finding as credential exposure and rotate before merging.
- Before using any real or sensitive data, review data flows to the database, Chroma/vector store, logs, backups, and configured LLM providers. Confirm contractual, privacy, residency, retention, encryption, and access-control requirements with the responsible organization.
- The application does not itself provide encryption-at-rest policy, managed key rotation, data retention/deletion workflows, tenant isolation, or a compliance certification. Configure these controls in the deployment and its managed services before any production use.
- Treat generated plans, model outputs, audit events, reports, prompts, and retrieved note excerpts as sensitive data if they are derived from sensitive inputs.

## Verification

The API security regression tests cover token verification and role extraction, deny-by-default behavior, role restrictions, rejection of caller-supplied actor identity, verified-subject attribution, public health/service-key behavior, body-size rejection, and route throttling:

```bash
python -m unittest discover -s tests -p test_api_security.py -v
```

The chat tool tests cover prompt-injection delimiter behavior and ensure retrieved chart text is returned as untrusted context:

```bash
python -m unittest discover -s tests -p test_chat_tools.py -v
```

Run these suites after modifying authentication middleware, route policies, request limits, decision schemas, actor attribution, RAG retrieval, or chat tool dispatch. Tests using mocked JWKS validate application behavior; deployment readiness also requires testing against the actual identity provider and its configured access-token claims.

## Deployment checklist

- [ ] Use `AUTH_MODE=oidc` for deployed user access; do not expose the shared-key demo mode as an identity system.
- [ ] Configure and test issuer, audience, JWKS URL, role claim, token lifetime, and SPA scopes with the identity provider.
- [ ] Register only the required SPA redirect/logout URLs and API audience; use HTTPS outside localhost.
- [ ] Use a public SPA client with PKCE and no client secret. Never bundle service credentials in the frontend.
- [ ] Restrict `CORS_ALLOWED_ORIGINS` to trusted app origins.
- [ ] Configure request-size and route-rate limits appropriate for the environment; use gateway/WAF or Redis-backed limits for horizontally scaled deployments.
- [ ] Store secrets in a managed secret store, rotate them, and use separate credentials per environment.
- [ ] Keep CI secret scanning enabled and rotate any credential flagged by secret scanning.
- [ ] Restrict database, vector-store, model-provider, filesystem, and log access; configure backups and retention.
- [ ] Review roles and route policy with the organization responsible for access governance.
- [ ] Re-run prompt-injection and RAG regression tests after changing prompts, retrieval, or tool schemas.
- [ ] Add operational monitoring and alerts for auth failures, audit-write failures, dependency outages, and unusual access.
- [ ] Complete security, privacy, clinical safety, and compliance reviews before any use with real patients or PHI.

## Reporting a vulnerability

Do not include real patient information, credentials, or access tokens in a bug report. Report security issues privately to the repository maintainers or the organization's designated security contact. Include the affected component, impact, and a minimal synthetic reproduction when possible. Allow maintainers time to investigate and release a fix before public disclosure.
