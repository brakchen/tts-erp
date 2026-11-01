import type { ReactNode } from 'react';

/** Page-specific purpose and optional actions; navigation/title remain in AppShell. */
export default function PageIntro({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: ReactNode }) {
  return <section className="erp-intro">
    <div className="min-w-0"><p className="erp-eyebrow">{eyebrow}</p><h2>{title}</h2><p className="erp-description">{description}</p></div>
    {children && <div className="erp-intro-actions">{children}</div>}
  </section>;
}
