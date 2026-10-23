# ERP frontend

React/TypeScript/Vite/free HeroUI, progressively serving the existing FastAPI page URLs. P0/P1 covers
`dashboard` and `intercept-requests`; the remaining ERP pages still use their existing renderers.

## Install, build and test

Use Node **22.22.2 or newer in the 22.x line** (validated on 22.23.3). From the repository root:

```bash
npm ci --include=dev --prefix frontend
bash scripts/build_frontend.sh
bash scripts/test_isolated.sh frontend
bash scripts/test_isolated.sh api tests/api/test_frontend_pages.py
bash scripts/test_isolated.sh browser tests/browser/test_frontend_pilots.py tests/browser/test_frontend_dev.py
```

Do not run Vitest/npm test/pytest directly. The isolated wrapper creates a per-run DB clone even for
mock-only frontend checks. `scripts/test.sh` performs the current build before its supported unit/fast/
domain selections; `tests/frontend/test_components.py` invokes Vitest inside that isolated test run.
The fast baseline includes the adapter and current-build browser tests. Missing dependencies/builds
are failures, not skipped coverage. Production-mode shells omit dev dependencies by default, hence the
explicit `--include=dev`; the component adapter explicitly uses `NODE_ENV=test`.

## Development

`npm run dev --prefix frontend` binds Vite to loopback. For a deliberately prepared **local** FastAPI,
set `TTS_ERP_FRONTEND_DEV_ORIGIN` to its loopback Vite origin (including port). Open the protected
FastAPI page, not an independent frontend login/origin. Root path, menu and permission authority remain
on FastAPI; the renderer injects the official React refresh preamble and Vite client. The browser test
starts disposable local services and verifies a real module hot update; it never targets live :9877.
Do not edit production `.env`, open wildcard CORS, or expose keys through `VITE_*`.

## Artifacts and ownership

`build_frontend.sh` typechecks, builds `frontend/dist`, then packages all Vite outputs (including
`.vite/manifest.json` and `.vite/license.md`) into the ignored `tts_erp_v2/static/frontend/<releaseId>/`.
The content-addressed ID and `static/frontend/release.json` select one coherent package. Older release
assets are retained; packaging does not remove them. Copy the complete assets/metadata with the same
backend release; production does not run Node/Vite or install npm dependencies. Packaging is not a
production deployment command or a guarantee of zero-downtime switching.

Bootstrap IDs: `frontend-bootstrap` / `frontend-root`. `AppShell` is shared, navigation metadata comes
from the server, and page modules own content only. Preserve the single backend migration set; missing
assets fail explicitly rather than falling back to legacy HTML. Native HeroUI styling is imported once;
there is no Bootstrap/legacy JS in migrated documents. See the approved standard and P0/P1 checkpoint:

- [Frontend standard](../docs/guides/frontend-development.md)
- [Migration design](../docs/plans/frontend-progressive-migration.md)
- [P0/P1 implementation](../docs/plans/frontend-p01-implementation.md)
- [Dependencies and licenses](NOTICE.md)
