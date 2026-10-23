"use client";

import Link from "next/link";

export default function WorkspaceError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <main className="workspace-gate"><section className="gate-card" aria-labelledby="workspace-error-heading"><p className="eyebrow">Workspace</p><h1 id="workspace-error-heading">This view could not be loaded</h1><p className="muted">The workspace UI hit an unexpected error. Sample data does not imply that any agent task has failed.</p><button className="button" type="button" onClick={reset}>Try again</button><Link className="text-link" href="/office">Return to office</Link></section></main>;
}
