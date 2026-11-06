function assertLocalPath(path: string): void {
  const decoded = decodeURIComponent(path.split(/[?#]/, 1)[0]);
  if (!decoded.startsWith('/') || decoded.startsWith('//') || /[\\\u0000-\u001f\u007f]/.test(decoded)
    || decoded.split('/').some((part) => part === '.' || part === '..')) {
    throw new Error('Expected a same-origin absolute path without traversal');
  }
}

/** Add the server-owned deployment prefix once; never accept external URLs. */
export function pathFor(rootPath: string, path: string): string {
  const root = rootPath.replace(/\/+$/, '');
  if (root) {
    assertLocalPath(root);
    if (/[?#]/.test(root)) throw new Error('Deployment prefix cannot contain a query or fragment');
  }
  assertLocalPath(path);
  const pathname = path.split(/[?#]/, 1)[0];
  return root && pathname !== root && !pathname.startsWith(`${root}/`) ? `${root}${path}` : path;
}
