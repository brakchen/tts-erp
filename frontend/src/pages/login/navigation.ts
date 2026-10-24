/** Native full-page navigation; no credentials or session data enter the URL. */
export function leaveLogin(path: string): void { window.location.assign(path); }
