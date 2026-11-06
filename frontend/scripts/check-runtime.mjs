// npm/node-semver 7.8.6 (ISC), https://github.com/npm/node-semver
// One engine declaration, including npm prerelease/range semantics.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import semver from 'semver';

export function checkRuntime(version = process.version) {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  if (!semver.satisfies(version, manifest.engines.node)) {
    throw new Error(`Frontend requires Node ${manifest.engines.node}; found ${version}. Select a supported Node, then npm ci --prefix frontend.`);
  }
  return version;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { console.log(checkRuntime()); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
