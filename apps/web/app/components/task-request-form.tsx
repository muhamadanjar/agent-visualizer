"use client";

import { sampleAgents, sampleTaskStages, sampleTasks } from "../lib/sample-data";
import { sampleProjects, sampleDefinitions, sampleSkills } from "../lib/ui-preview-data";
import { DisabledWorkAction, PageHeader, SampleNotice, StatusPill } from "./workspace-components";

export function TaskRequestForm() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Work coordination" title="New task" description="Compose a task request preview. It will not be queued, assigned, or sent to a runtime." />
      <SampleNotice />
      <form className="content-section draft-form" onSubmit={(event) => event.preventDefault()}>
        <div className="section-heading"><h2>Task specification</h2><StatusPill>Draft only</StatusPill></div>
        <label className="preview-field">Title<input name="title" required placeholder="Describe the outcome" /></label>
        <label className="preview-field">Goal<textarea name="goal" rows={4} required placeholder="What should the assigned agent accomplish?" /></label>
        <label className="preview-field">Project<select name="project" defaultValue=""><option value="" disabled>Select sample project</option>{sampleProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label className="preview-field">Agent instance<select name="agentInstance" defaultValue=""><option value="" disabled>Select sample instance</option>{sampleAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · {agent.role}</option>)}</select></label>
        <label className="preview-field">Agent definition and version<select name="definition" defaultValue=""><option value="" disabled>Select sample definition</option>{sampleDefinitions.map((definition) => <option key={definition.id} value={definition.id}>{definition.name} · {definition.version}</option>)}</select></label>
        <label className="preview-field">Skill references<select name="skill" defaultValue=""><option value="">No sample skill selected</option>{sampleSkills.map((skill) => <option key={skill.id} value={skill.id}>{skill.name} · {skill.version}</option>)}</select></label>
        <label className="preview-field">Inputs and resources<textarea name="resources" rows={3} placeholder="Paths or text inputs; no repository scope is connected" /></label>
        <label className="preview-field">Acceptance criteria<textarea name="criteria" rows={4} required placeholder="One verifiable outcome per line" /></label>
        <label className="preview-field">Blocked by<select name="blockedBy" defaultValue=""><option value="">No dependency</option>{sampleTasks.map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}</select></label>
        <p className="field-note">Cycle detection, project-resource validation, and post-start revision behavior are unavailable. This list does not validate dependency safety.</p>
        <fieldset className="draft-fieldset">
          <legend>Execution boundaries</legend>
          <label className="preview-field">Maximum duration (minutes)<input inputMode="numeric" type="number" min="1" placeholder="Not configured" /></label>
          <label className="preview-field">Maximum child runs<input inputMode="numeric" type="number" min="0" placeholder="Not configured" /></label>
          <p className="field-note">Limits are not validated or enforced. Concurrency and delegation-depth policy are unavailable.</p>
        </fieldset>
        <section className="connection-placeholder" aria-labelledby="task-preflight-heading">
          <div className="section-heading"><h2 id="task-preflight-heading">Preflight preview</h2><StatusPill tone="amber">Blocked · not evaluated</StatusPill></div>
          <ul className="preflight-list"><li>Project access and repository scope: not configured</li><li>Agent definition, skill versions, and dependencies: not validated</li><li>Provider and runtime capabilities: not connected</li><li>Queue capacity and idempotency: not available</li></ul>
        </section>
        <div className="form-actions">
          <DisabledWorkAction reason="Task persistence, project scope, preflight, queueing, and runtime dispatch are not connected.">Create task</DisabledWorkAction>
          <span className="muted">Draft values remain in this browser form and disappear on navigation.</span>
        </div>
      </form>
    </div>
  );
}
