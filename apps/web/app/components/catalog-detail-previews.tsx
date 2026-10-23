"use client";

import Link from "next/link";
import type { SampleSkill } from "../lib/ui-preview-data";
import { DisabledWorkAction, PageHeader, SampleNotice, StatusPill } from "./workspace-components";


export function SkillDetailPreview({ skill }: { skill: SampleSkill }) {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Skill resource" title={skill.name} description="Inspect a text-only skill reference. A skill does not grant tools, file access, network access, or credentials.">
        <Link className="button button-secondary" href="/skills">Back to skills</Link>
      </PageHeader>
      <SampleNotice />
      <div className="settings-grid"><section className="content-section settings-card"><div className="section-heading"><h2>Version and dependencies</h2><StatusPill tone={skill.status === "Declared dependency unavailable" ? "amber" : "neutral"}>{skill.status}</StatusPill></div><div className="settings-row"><span>Version</span><strong>{skill.version}</strong></div><div className="settings-row"><span>Declared dependencies</span><strong>{skill.dependencies.length ? skill.dependencies.join(", ") : "None declared"}</strong></div><div className="settings-row"><span>Sample definitions</span><strong>{skill.attachedTo.length ? skill.attachedTo.join(", ") : "Not attached"}</strong></div><p className="field-note">No dependency install or runtime-compatibility check has been performed.</p></section><section className="content-section settings-card"><div className="section-heading"><h2>Import safety</h2><StatusPill>Text only</StatusPill></div><p>Scripts, package hooks, and network references are not executed or fetched by this preview. Compatibility is unknown unless a declared dependency is independently validated.</p></section></div>
      <section className="content-section draft-form"><div className="section-heading"><h2>Text content</h2><StatusPill>Sample</StatusPill></div><p>{skill.description}</p><label className="preview-field">Instructions<textarea rows={10} defaultValue={`# ${skill.name}\n\n${skill.description}\n\nDo not assume tools, permissions, or dependencies are available.`} spellCheck={false} /></label><div className="form-actions"><DisabledWorkAction reason="Skill version persistence and compatibility validation are not connected.">Save new version</DisabledWorkAction><span className="muted">The text is a display fixture, not an executable skill package.</span></div></section>
    </div>
  );
}
