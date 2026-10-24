# ERP frontend upstream dependencies

This is the free/open-source HeroUI React frontend, not HeroUI Pro. No paid template code is included.
Versions are pinned in `package.json` and resolved completely in `package-lock.json`.

| Dependency | Pinned version | License | Upstream |
| --- | --- | --- | --- |
| React / React DOM | 19.3.1 | MIT | https://github.com/facebook/react |
| uPlot | 1.6.32 | MIT | https://github.com/leeoniya/uPlot |
| uPlot React wrapper | 1.2.4 | MIT | https://github.com/skalinichev/uplot-wrappers |
| HeroUI React / Styles | 3.2.6 | MIT | https://github.com/heroui-inc/heroui |
| Tailwind CSS / Vite plugin | 4.3.3 | MIT | https://github.com/tailwindlabs/tailwindcss |
| Vite | 8.3.5 | MIT | https://github.com/vitejs/vite |
| Vite React plugin | 6.1.2 | MIT | https://github.com/vitejs/vite-plugin-react |
| TypeScript | 7.0.2 | Apache-2.0 | https://github.com/microsoft/TypeScript |
| Vitest | 5.0.5 | MIT | https://github.com/vitest-dev/vitest |
| jsdom | 30.1.3 | MIT | https://github.com/jsdom/jsdom |
| Testing Library React / user-event / DOM / jest-dom | 16.3.3 / 14.6.8 / 10.4.2 / 7.0.1 | MIT | https://github.com/testing-library |

HeroUI's maintained React Aria peers are resolved by npm; this project does not reimplement their focus,
keyboard, selection, or overlay primitives. Additional peer and transitive licenses are recorded by the
Vite built-in `build.license` facility in `dist/.vite/license.md`; packaging preserves that file in each
self-hosted release. It contains the bundled dependencies' attribution/license text and accompanies the
JavaScript/CSS assets. Build/test-only packages are not installed on the production server.

Initial implementation checks: clean pinned installation, Node 22.23.3, TypeScript/build and `npm audit`
(including development dependencies) passed with zero reported vulnerabilities on 2026-10-23. This is a
point-in-time result, not a promise that later audits or upstream changes will stay clean. The P2 chart
addition on 2026-10-24 adds exactly the two pinned packages above (neither declares runtime dependencies);
the wrapper declares React >=16.8.6 and uPlot ^1.6.32 peers. Installed MIT license texts were checked and
`npm audit`, including development packages, reported zero vulnerabilities. This is dependency evidence,
not native chart/StrictMode/resize/browser acceptance; those remain page-level gates before activation.
Use `npm ci --include=dev`, retain the lock, and review dependency upgrades separately.
