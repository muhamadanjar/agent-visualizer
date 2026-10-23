"use client";

import { useState } from "react";
import { DisabledWorkAction, PageHeader, SampleNotice, StatusPill } from "./workspace-components";

const MAX_SOURCE_BYTES = 1_048_576;

export function SkillImportPreview() {
  const [source, setSource] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [name, setName] = useState("");
  async function loadFile(file?: File) {
    setFileError(null);
    if (!file) return;
    if (!/\.(md|markdown|txt)$/i.test(file.name) || file.size > MAX_SOURCE_BYTES) {
      setFileError("Choose a Markdown/text file no larger than 1 MiB.");
      return;
    }
    try {
      setSource(await file.text());
      setName(file.name.replace(/\.(md|markdown|txt)$/i, ""));
    } catch {
      setFileError("The browser could not read this file as text.");
    }
  }
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Skill library" title="Import text skill" description="Preview a local text resource as data only. Import does not install dependencies or grant tools." />
      <SampleNotice />
      <form className="content-section draft-form" onSubmit={(event) => event.preventDefault()}>
        <div className="section-heading"><h2>Local skill draft</h2><StatusPill>Not uploaded</StatusPill></div>
        <label className="preview-field">Choose Markdown or text<input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" onChange={(event) => void loadFile(event.target.files?.[0])} /></label>
        {fileError ? <p className="inline-error" role="alert">{fileError}</p> : null}
        <label className="preview-field">Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Skill name" /></label>
        <label className="preview-field">Text instructions<textarea value={source} onChange={(event) => setSource(event.target.value)} rows={15} spellCheck={false} placeholder="Paste or select a text resource" /></label>
        <label className="preview-field">Declared dependencies<textarea rows={3} placeholder="Optional package/tool declarations; no automatic install or compatibility assumption" /></label>
        <section className="connection-placeholder"><div className="section-heading"><h2>Safety boundary</h2><StatusPill tone="amber">Not validated</StatusPill></div><p>Scripts are not executed, packages are not installed, and network references are not fetched. A declared dependency is not a verified capability.</p></section>
        <div className="form-actions"><DisabledWorkAction reason="Skill version persistence and dependency validation are not connected.">Save skill version</DisabledWorkAction><span className="muted">The 1 MiB-bounded draft stays in page memory only and is lost on navigation.</span></div>
      </form>
    </div>
  );
}
