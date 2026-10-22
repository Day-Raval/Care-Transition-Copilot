# Changelog

All notable project changes should be summarized here. Keep detailed design and
run notes in `docs/` or `results/`.

## Unreleased

### Repository maintenance

- Renamed `Docs/` to `docs/`, grouped architecture and requirements documents there,
  and updated documentation links, Docker inputs, and release-package paths.
- Removed the unused root placeholder `main.py`; application entry points remain unchanged.
- Documented direct dependencies, their compatibility mirror, and lockfile maintenance;
  aligned contributor setup with validated Python/frontend lockfiles.
- Added an explicit pytest-cache ignore rule. No dependency versions changed.

### Clinician workflow regression tests

- Extended Playwright coverage for the complete clinician journey, rapid patient
  switching, repeated approval clicks, network-failure retries, and orderly API
  restart recovery without changing application behavior. All 12 browser tests pass.
- Documented the unresolved synthetic retrieval-index failure after forced termination.
- Include these test changes in the next release that delivers a user-facing fix
  or resolves the recovery issue; no separate version or package release is planned.

### MVP delivery and documentation (October 2026)

- Added validated Windows/Linux local deployment, persistent clinician assessments,
  decisions and reports, deployment locking, and backup/restore.
- Improved patient names/history, episode selection, and rapid-selection handling;
  provider failures return safe errors while saved workflows remain accessible.
- Added checksummed release packages, locked dependency updates, readiness checks,
  and rollback on update failure.
- Added cross-platform Python/Chromium CI, Linux container tests, non-root runtime
  images, and tagged GHCR/GitHub Release publication automation with attestations.
  The new container publishing path still requires its own preview/tagged verification.
- Refreshed the MVP landing page and added backend, frontend, scripts, tests,
  documentation, and automation folder guides; removed an unused stakeholder image.

### Earlier documentation baseline

- Reworked the top-level README into a concise GitHub landing page.
- Moved the previous long README to `docs/README_DETAILED.md`.
- Added project hygiene files: license, contribution guide, code of conduct,
  CI workflow, model card, data card, and local check scripts.
- Kept both dependency workflows supported: `requirements.txt` for operational
  installs and `pyproject.toml` for uv-native project installs.

## Local MVP Baseline

- Implemented synthetic FHIR/HL7 ingestion, discharge episode construction,
  feature export, 30-day readmission target labeling, and baseline survival
  modeling.
- Added patient-scoped retrieval, section-aware note chunking, ChromaDB vector
  store build/validation, risk-gated agent orchestration, reasoning and critique
  agents, and tool-calling chat.
- Added FastAPI serving, API-key/OIDC auth, RBAC checks, audit logging,
  persistence options, Kafka integration, notifications, follow-up/reminder
  records, metrics, and a React clinician workflow.
