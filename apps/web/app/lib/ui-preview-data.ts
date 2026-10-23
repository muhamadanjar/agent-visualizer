export type SampleProject = {
  id: string;
  name: string;
  summary: string;
  repository: string;
  branch: string;
  access: string;
  agents: number;
  tasks: number;
};

export const sampleProjects: SampleProject[] = [
  { id: "project-atlas", name: "Atlas workspace", summary: "Adapter exploration and workspace flow previews.", repository: "Not connected", branch: "Not available", access: "Sample scope only", agents: 3, tasks: 4 },
  { id: "project-garden", name: "Garden workspace", summary: "Review and documentation examples for the UI preview.", repository: "Not connected", branch: "Not available", access: "Sample scope only", agents: 1, tasks: 2 },
];

export type SampleDefinition = {
  id: string;
  name: string;
  description: string;
  version: string;
  state: "Current sample" | "Archived sample";
  skills: string[];
  permission: string;
  updated: string;
};

export const sampleDefinitions: SampleDefinition[] = [
  { id: "definition-research", name: "Research agent", description: "Collects and compares evidence for a bounded project task.", version: "v3 · Sample", state: "Current sample", skills: ["Research notes"], permission: "Read-only sample scope", updated: "Sample · 10:20" },
  { id: "definition-implementation", name: "Implementation agent", description: "Plans implementation work and reports a reviewable result.", version: "v2 · Sample", state: "Current sample", skills: ["TypeScript review"], permission: "No repository access", updated: "Sample · yesterday" },
  { id: "definition-review", name: "Review agent", description: "Inspects a proposed change and records actionable findings.", version: "v1 · Sample", state: "Current sample", skills: [], permission: "Read-only sample scope", updated: "Sample · yesterday" },
];

export type SampleSkill = {
  id: string;
  name: string;
  description: string;
  version: string;
  dependencies: string[];
  attachedTo: string[];
  status: "No dependencies declared" | "Declared dependency unavailable";
};

export const sampleSkills: SampleSkill[] = [
  { id: "skill-research", name: "Research notes", description: "A text-only outline for recording sources and comparison criteria.", version: "v1 · Sample", dependencies: [], attachedTo: ["Research agent"], status: "No dependencies declared" },
  { id: "skill-typescript", name: "TypeScript review", description: "A sample checklist for checking boundaries and type behavior.", version: "v2 · Sample", dependencies: ["Repository inspection tool · unavailable"], attachedTo: ["Implementation agent"], status: "Declared dependency unavailable" },
  { id: "skill-docs", name: "Documentation pass", description: "A sample checklist for terminology, examples, and requirement traceability.", version: "v1 · Sample", dependencies: [], attachedTo: [], status: "No dependencies declared" },
];

export const sampleAuditEntries = [
  { time: "Sample · 10:42", actor: "Owner preview", action: "Viewed run detail", target: "run-sample-build", source: "Browser presentation fixture" },
  { time: "Sample · 10:38", actor: "Application preview", action: "Displayed approval example", target: "inbox-sample-approval", source: "Local fixture; no decision made" },
  { time: "Sample · yesterday", actor: "Owner preview", action: "Opened office overview", target: "scene-template-sample", source: "Local presentation state" },
];

export const sampleArtifacts = [
  { id: "artifact-outline", name: "adapter-boundary-outline.md", runId: "run-sample-build", provenance: "Sample artifact · local text preview", checksum: "Not available", created: "Sample · 10:28" },
  { id: "artifact-comparison", name: "provider-route-notes.md", runId: "run-sample-research", provenance: "Sample artifact · not produced by a runtime", checksum: "Not available", created: "Sample · 10:39" },
];
