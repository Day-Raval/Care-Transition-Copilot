# Documentation index

Start with the [MVP overview and demo walkthrough](../README.md).

Architecture, requirements, and the original proposal live here. The repository
root keeps contributor, security, license, and release-history documents.

## Run and operate

- [Local deployment](LOCAL_DEPLOYMENT.md): provisioning, readiness, persistence, logs, backup, and restore.
- [Containers](CONTAINERS.md): images, Compose, state mounts, GHCR, and publishing verification limits.
- [CI/CD](CI_CD.md): quality gates, previews, tagged releases, native updates, and rollback.
- [Scripts](../scripts/README.md): launchers and diagnostics.
- [Tests](../tests/README.md): local checks, browser fixtures, and coverage limits.

## Understand the implementation

- [Architecture](ARCHITECTURE.md) and [backend modules/pipeline](../src/README.md).
- [Frontend](../web/README.md): screens, proxy, authentication, and builds.
- [Detailed implementation notes](README_DETAILED.md): expanded background; use the deployment/CI/container guides above for current operational procedures.
- [FHIR/HL7 data documentation](data_documentation.md).
- [Database](../database/README.md), [observability](../observability/README.md), [results](../results/README.md), and [reports](../reports/README.md).

## Scope and governance

- [Data card](DATA_CARD.md) and [model card](MODEL_CARD.md): synthetic data, diagnostic scoring, and evaluation limits.
- [Security](../SECURITY.md), [contributing](../CONTRIBUTING.md), and [changelog](../CHANGELOG.md).
- [Product requirements](PRD.md) and [software requirements](SRS.md): design context, not evidence that every planned capability is implemented.
- [Original project proposal](Care_Transition_Copilot_Proposal.docx).

`readme_workflow.svg` and `readme_clinician_review.svg` are the main README previews. Use synthetic data only in shared documentation and artifacts.
