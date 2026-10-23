// Package Vite's maintained manifest/asset format; retain old content-addressed releases.
import { createHash, randomUUID } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const source = path.resolve(process.argv[2] ?? path.join(frontend, 'dist'));
const target = path.resolve(process.argv[3] ?? path.join(frontend, '../tts_erp_v2/static/frontend'));

async function filesIn(directory, prefix = '') {
  const files = [];
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const name = path.posix.join(prefix, item.name);
    if (item.isSymbolicLink()) throw new Error('Build artifacts cannot contain symlinks');
    if (item.isDirectory()) files.push(...await filesIn(path.join(directory, item.name), name));
    else if (item.isFile()) files.push(name);
    else throw new Error('Build artifacts must be regular files');
  }
  return files.sort();
}

function validateManifest(manifest, files) {
  const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  if (!isRecord(manifest) || !isRecord(manifest['src/main.tsx']) || manifest['src/main.tsx'].isEntry !== true) {
    throw new Error('Missing Vite main entry');
  }
  const available = new Set(files);
  function asset(name, suffixes) {
    if (typeof name !== 'string' || !name || /[\\%?#:\x00-\x20]/.test(name)
        || name.split('/').some((part) => ['', '.', '..'].includes(part)) || !available.has(name)) {
      throw new Error('Missing or escaping frontend asset');
    }
    if (suffixes && !suffixes.some((suffix) => name.endsWith(suffix))) throw new Error('Invalid frontend asset type');
  }
  function list(record, key) {
    const value = Object.hasOwn(record, key) ? record[key] : [];
    if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) throw new Error('Invalid manifest list');
    return value;
  }
  // Mirror the serving edge: validate eager AND lazy closure before selecting it.
  // Inventory membership confines all paths to regular, non-symlink input files.
  const visited = new Set();
  const pending = ['src/main.tsx'];
  while (pending.length) {
    const key = pending.pop();
    if (visited.has(key)) continue;
    const record = manifest[key];
    if (!isRecord(record)) throw new Error('Missing manifest chunk');
    asset(record.file, ['.js', '.mjs']);
    for (const name of list(record, 'css')) asset(name, ['.css']);
    for (const name of list(record, 'assets')) asset(name);
    visited.add(key);
    pending.push(...list(record, 'imports'), ...list(record, 'dynamicImports'));
  }
}

try {
  if (target === source || target.startsWith(source + path.sep)) throw new Error('Output cannot be inside input');
  const files = await filesIn(source);
  const manifest = JSON.parse(await readFile(path.join(source, '.vite/manifest.json'), 'utf8'));
  validateManifest(manifest, files);
  const hash = createHash('sha256');
  for (const name of files) {
    const bytes = await readFile(path.join(source, name));
    hash.update(name + '\0' + bytes.length + '\0');
    hash.update(bytes);
  }
  const releaseId = 'build-' + hash.digest('hex').slice(0, 20);
  const release = path.join(target, releaseId);
  for (const name of files) {
    const output = path.join(release, name);
    await mkdir(path.dirname(output), { recursive: true });
    await copyFile(path.join(source, name), output);
  }
  // Select only a fully copied package. Never remove the previous release assets.
  const temporary = path.join(target, '.release-' + randomUUID() + '.tmp');
  await writeFile(temporary, JSON.stringify({ releaseId }) + '\n', { flag: 'wx' });
  await rename(temporary, path.join(target, 'release.json'));
  console.log(JSON.stringify({ releaseId }));
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Frontend packaging failed');
  process.exitCode = 1;
}
