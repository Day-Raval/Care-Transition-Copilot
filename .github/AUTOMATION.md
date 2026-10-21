# CI and release automation

[CI/CD procedure](../Docs/CI_CD.md) · [Container and GHCR guide](../Docs/CONTAINERS.md)

| Workflow | Triggers | Behavior |
| --- | --- | --- |
| `workflows/ci.yml` | Main pushes, pull requests, manual runs, reusable calls | Gitleaks history scan, Linux container tests, native Windows Python/browser tests, runtime build/import checks |
| `workflows/release.yml` | `v*` tags or manual preview | Rerun CI, require source in main history, package built UI/source with checksums, build runtime/CI images |

Only version-tag pushes publish GHCR images and a GitHub Release. Manual previews upload package artifacts without publication. Release publication waits for packaging and both image jobs.

## Environments and artifacts

- Python 3.12.10, Node 24.19.0, and uv 0.11.3 are pinned; dependencies use lockfiles.
- Linux tests/packaging use the Dockerfile's `ci` stage. Windows remains native to verify paths, launchers, and file locks.
- Browser tests use generated synthetic assets and deterministic provider/embedding fixtures without provider secrets.
- Test results/failure traces are retained seven days; package artifacts thirty days.
- Images target Linux AMD64. Tagged publication includes version/full-commit tags, SBOM/provenance, and digests. Stable tags update `latest`; prereleases do not.

Actions use pinned commit SHAs and read-only repository permissions by default. Tagged GitHub publication gets contents-write permission; the image job gets package-write permission. GHCR login runs only on version-tag pushes.

No workflow deploys to a remote host. Native updates use the local launcher; container updates replace the image. Configuration, models, data, credentials, and saved state remain outside published packages/images. See the container guide's dated review for the publishing verification still required.
