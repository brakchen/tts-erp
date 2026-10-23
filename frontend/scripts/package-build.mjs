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

try {
  if (target === source || target.startsWith(source + path.sep)) throw new Error('Output cannot be inside input');
  const files = await filesIn(source);
  const manifest = JSON.parse(await readFile(path.join(source, '.vite/manifest.json'), 'utf8'));
  const entry = manifest['src/main.tsx'];
  if (!entry || entry.isEntry !== true || !files.includes(entry.file)) throw new Error('Missing Vite main entry');
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
