# ERP frontend

React/TypeScript/Vite/free HeroUI serving the existing FastAPI page URLs. All 15 registered ERP
business pages and the anonymous login now use the shared frontend renderer. Source integration
and a built package do not by themselves mean this version has been deployed to production.

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

All 15 React business pages share the workspace shell, `PageIntro`, and `app/workspace.css` layout
classes. The shared palette is imported from `tts_erp_v2/static/css/tokens.css`, also consumed by
the shared `focused-spus` and `spu-roi` profitability module. The desktop navigation is dark green; surfaces,
filter panels, tables, and responsive content spacing use the same light theme. Component
interaction and state styling remain owned by HeroUI. No API or financial calculation is moved
into the presentation layer.

HeroUI and semantic frontend tables use `shared/tables.css` through public slot `className` props
or equivalent semantic native-table markup. The flat secondary variant avoids nested card
surfaces; scroll regions remain keyboard-focusable, row controls retain their accessible names,
and all original sort, expand, write-permission and recovery handlers are preserved.

The anonymous login uses the shared `AppShell` fullscreen layout, retaining one header and main
landmark without the business-page header spacing or content-width limit. Its brand panel and
scoped styles live in `src/pages/login/`; below 768px the composition becomes one column and
decorative content is reduced. HeroUI owns input, button, validation and focus behavior, including
the native `InputGroup` password toggle. Login requests, canonical return paths, throttling and
explicit session recovery continue to use the existing authentication contract.

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

Navigation visuals (brand, groups, menu rows, active indicator and global header) are owned by
`tts_erp_v2/static/css/navigation.css`, imported once by React. Both profitability URLs load
`src/pages/spu-profitability/`, including profile defaults, exact/persistent SPU scopes, all
current and projected metrics, nested price statistics and five evidence views. React owns
filters, requests and detail content; the existing self-hosted Tabulator 6.3.1 MIT instance
owns only its empty ref container, preserving frozen columns and server sorting. It does not
mount the retired whole-page kernel. Dates and scope controls precede summaries; detailed
metrics remain available through disclosures. See the [SPU migration record](../docs/plans/spu-react-migration-2026-11-01.md).

Focused SPUs remain visible as removable tags inside the search field, as requested by the user.
The existing HeroUI 3.2.6 / MIT [TagGroup](https://heroui.com/en/docs/react/components/tag-group)
owns tag keyboard/removal interaction. A removal uses the existing explicit single-mutation
handler and changes the displayed membership only after its receipt; readonly, failed-write
readback and Retry-After guards remain in that handler. No separate collapsed selected list.

- [Frontend standard](../docs/guides/frontend-development.md)
- [Migration design](../docs/plans/frontend-progressive-migration.md)
- [P0/P1 implementation](../docs/plans/frontend-p01-implementation.md)
- [Dependencies and licenses](NOTICE.md)
