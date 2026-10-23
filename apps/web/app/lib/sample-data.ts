export type AgentPresence = "Idle" | "At desk" | "Needs attention";
export type TaskStage = "Backlog" | "Ready" | "In progress" | "Blocked / Waiting" | "In review" | "Accepted";
export type RunStatus = "Running" | "Succeeded" | "Failed" | "Canceled";

export type SampleAgent = {
  id: string;
  name: string;
  role: string;
  project: string;
  room: string;
  presence: AgentPresence;
  summary: string;
  color: string;
  position: [number, number];
  runId: string | null;
};

export type SampleTask = {
  id: string;
  title: string;
  project: string;
  goal: string;
  inputs: string[];
  acceptanceCriteria: string[];
  blockedBy: string[];
  agentId: string | null;
  stage: TaskStage;
  runStatus: RunStatus | null;
  updated: string;
  runId: string | null;
};

export type SampleRun = {
  id: string;
  taskId: string;
  agentId: string;
  status: RunStatus;
  runtime: string;
  started: string;
  events: Array<{ time: string; type: string; detail: string }>;
};

export type SampleInboxItem = {
  id: string;
  kind: "Input" | "Approval" | "Blocked" | "Failed" | "Stale connection" | "Limit";
  title: string;
  detail: string;
  source: string;
  runId: string;
  state: "Open" | "Resolved";
  actionScope: string;
  parameters: string;
};

export const sampleAgents: SampleAgent[] = [
  {
    id: "agent-research",
    name: "Mira",
    role: "Research agent",
    project: "Atlas workspace",
    room: "Research room",
    presence: "At desk",
    summary: "Comparing three public API options for the sample project.",
    color: "#56775f",
    position: [-5.1, -2.8],
    runId: "run-sample-research",
  },
  {
    id: "agent-builder",
    name: "Raka",
    role: "Implementation agent",
    project: "Atlas workspace",
    room: "Build room",
    presence: "At desk",
    summary: "Preparing a sample implementation outline.",
    color: "#b8794f",
    position: [4.8, -2.5],
    runId: "run-sample-build",
  },
  {
    id: "agent-reviewer",
    name: "Sora",
    role: "Review agent",
    project: "Garden workspace",
    room: "Review room",
    presence: "Needs attention",
    summary: "A sample review is waiting for an owner decision.",
    color: "#6686a0",
    position: [4.5, 2.8],
    runId: "run-sample-review",
  },
  {
    id: "agent-available",
    name: "Nara",
    role: "General agent",
    project: "Atlas workspace",
    room: "Shared studio",
    presence: "Idle",
    summary: "Available in this sample workspace.",
    color: "#9b8b5e",
    position: [-4.6, 2.6],
    runId: null,
  },
];

export const sampleTasks: SampleTask[] = [
  { id: "task-sample-research", title: "Compare provider routes", project: "Atlas workspace", goal: "Compare candidate provider routes using public information only.", inputs: ["No repository or private resources attached."], acceptanceCriteria: ["List observable adapter/protocol differences.", "Mark unsupported capabilities as unknown."], blockedBy: [], agentId: "agent-research", stage: "In progress", runStatus: "Running", updated: "Sample · moments ago", runId: "run-sample-research" },
  { id: "task-sample-build", title: "Draft adapter boundary", project: "Atlas workspace", goal: "Draft a separation between model-provider and runtime-adapter configuration.", inputs: ["Sample requirements only."], acceptanceCriteria: ["Provider and runtime are separate concepts.", "No secrets appear in the sample output."], blockedBy: [], agentId: "agent-builder", stage: "In review", runStatus: "Succeeded", updated: "Sample · 10:42", runId: "run-sample-build" },
  { id: "task-sample-review", title: "Review access notes", project: "Garden workspace", goal: "Review a sample access boundary and identify unresolved scope.", inputs: ["Sample access notes; no connected repository."], acceptanceCriteria: ["Identify missing project scope.", "Do not mark unavailable evidence as verified."], blockedBy: ["task-sample-build"], agentId: "agent-reviewer", stage: "Blocked / Waiting", runStatus: "Running", updated: "Sample · 10:38", runId: "run-sample-review" },
  { id: "task-sample-ready", title: "Map settings screen", project: "Atlas workspace", goal: "Map the settings presentation for a preview workspace.", inputs: ["Local UI fixture."], acceptanceCriteria: ["Separate provider and runtime settings.", "Show unavailable persistence."], blockedBy: [], agentId: null, stage: "Ready", runStatus: null, updated: "Sample · 10:20", runId: null },
  { id: "task-sample-backlog", title: "Document scene layout", project: "Garden workspace", goal: "Record a proposed room layout for a sample scene.", inputs: ["No saved scene version."], acceptanceCriteria: ["Label layout as proposed.", "Do not change project permissions."], blockedBy: [], agentId: null, stage: "Backlog", runStatus: null, updated: "Sample · 09:55", runId: null },
  { id: "task-sample-accepted", title: "Create project outline", project: "Atlas workspace", goal: "Prepare a sample outline for a small project.", inputs: ["Sample project description."], acceptanceCriteria: ["Outline includes scope and unknowns."], blockedBy: [], agentId: "agent-builder", stage: "Accepted", runStatus: "Succeeded", updated: "Sample · yesterday", runId: "run-sample-build" },
];

export const sampleRuns: SampleRun[] = [
  {
    id: "run-sample-research",
    taskId: "task-sample-research",
    agentId: "agent-research",
    status: "Running",
    runtime: "Sample runtime · not connected",
    started: "Sample · 10:36",
    events: [
      { time: "10:36", type: "Task accepted", detail: "Sample run started in the Atlas workspace." },
      { time: "10:37", type: "Activity", detail: "A sample provider comparison was opened." },
      { time: "10:39", type: "Output", detail: "Three candidate routes were added to the sample notes." },
    ],
  },
  {
    id: "run-sample-build",
    taskId: "task-sample-build",
    agentId: "agent-builder",
    status: "Succeeded",
    runtime: "Sample runtime · not connected",
    started: "Sample · 10:21",
    events: [
      { time: "10:21", type: "Task accepted", detail: "Sample run started in the Atlas workspace." },
      { time: "10:28", type: "Artifact", detail: "A sample adapter outline was attached." },
      { time: "10:42", type: "Run result", detail: "The sample runtime reported completion; no real task ran." },
    ],
  },
  {
    id: "run-sample-review",
    taskId: "task-sample-review",
    agentId: "agent-reviewer",
    status: "Running",
    runtime: "Sample runtime · not connected",
    started: "Sample · 10:38",
    events: [
      { time: "10:38", type: "Task accepted", detail: "Sample run started in the Garden workspace." },
      { time: "10:40", type: "Approval requested", detail: "The sample approval action is disabled in this UI preview." },
    ],
  },
];

export const sampleInboxItems: SampleInboxItem[] = [
  { id: "inbox-sample-input", kind: "Input", title: "Clarify comparison scope", detail: "A sample agent needs a choice about which provider routes to compare.", source: "Mira · task-sample-research", runId: "run-sample-research", state: "Open", actionScope: "Owner input", parameters: "No model message will be sent from this preview." },
  { id: "inbox-sample-approval", kind: "Approval", title: "Review a proposed action", detail: "The sample request would inspect a repository path that is not connected.", source: "Sora · task-sample-review", runId: "run-sample-review", state: "Open", actionScope: "Read repository file", parameters: "Path: unavailable · project scope: unavailable · invocation ID: unavailable" },
  { id: "inbox-sample-blocked", kind: "Blocked", title: "Repository access unavailable", detail: "The sample task has no connected repository scope.", source: "Sora · task-sample-review", runId: "run-sample-review", state: "Open", actionScope: "Project resource", parameters: "No repository or workspace is configured." },
  { id: "inbox-sample-failed", kind: "Failed", title: "Sample attempt needs review", detail: "Illustrative failure state; no execution attempt was made.", source: "Raka · task-sample-build", runId: "run-sample-build", state: "Open", actionScope: "Inspect sample result", parameters: "Error details: not available" },
  { id: "inbox-sample-stale", kind: "Stale connection", title: "Runtime freshness unavailable", detail: "No heartbeat source is connected. This is not an observed outage.", source: "Mira · task-sample-research", runId: "run-sample-research", state: "Open", actionScope: "Inspect connection", parameters: "Last heartbeat: not available" },
  { id: "inbox-sample-limit", kind: "Limit", title: "Capacity data unavailable", detail: "No live capacity or execution-limit measurement is connected.", source: "Workspace · sample capacity", runId: "run-sample-build", state: "Open", actionScope: "Inspect limits", parameters: "Active runs and budget: not available" },
  { id: "inbox-sample-resolved", kind: "Blocked", title: "Example marked resolved", detail: "Historical sample resolution; no user decision or runtime acknowledgement occurred.", source: "Owner preview · sample history", runId: "run-sample-build", state: "Resolved", actionScope: "Historical example", parameters: "Resolution evidence: not available" },
];

export const sampleTaskStages: TaskStage[] = ["Backlog", "Ready", "In progress", "Blocked / Waiting", "In review", "Accepted"];

export function getSampleAgent(id: string): SampleAgent | undefined {
  return sampleAgents.find((agent) => agent.id === id);
}

export function getSampleTask(id: string): SampleTask | undefined {
  return sampleTasks.find((task) => task.id === id);
}

export function getSampleRun(id: string): SampleRun | undefined {
  return sampleRuns.find((run) => run.id === id);
}
