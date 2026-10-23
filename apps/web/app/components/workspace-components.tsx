import Link from "next/link";
import { useId, type ReactNode } from "react";

export function PageHeader({ eyebrow, title, description, children }: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {children ? <div className="page-header-actions">{children}</div> : null}
    </header>
  );
}

export function SampleNotice() {
  return (
    <aside className="sample-notice" aria-label="Status data">
      <span className="sample-notice-mark" aria-hidden="true">S</span>
      <p><strong>Sample data.</strong> Catatan lokal untuk pratinjau UI; tidak terhubung ke agent, task, run, runtime, atau approval nyata.</p>
    </aside>
  );
}

export function StatusPill({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

export function DisabledWorkAction({ children, reason = "Tersedia setelah domain API dan runtime nyata terhubung." }: { children: ReactNode; reason?: string }) {
  const id = useId();
  return (
    <span className="disabled-action-wrap">
      <button className="button button-secondary" type="button" disabled aria-describedby={id} title={reason}>{children}</button>
      <span className="visually-hidden" id={id}>{reason}</span>
    </span>
  );
}

export function ViewLink({ href, children, className = "text-link" }: { href: string; children: ReactNode; className?: string }) {
  return <Link className={className} href={href}>{children}</Link>;
}

export function SectionHeading({ title, count, children }: { title: string; count?: number; children?: ReactNode }) {
  return (
    <div className="section-heading">
      <div><h2>{title}</h2>{count !== undefined ? <span className="section-count">{count}</span> : null}</div>
      {children}
    </div>
  );
}
