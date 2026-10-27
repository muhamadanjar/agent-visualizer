# Agent Office

This glossary distinguishes reusable agent templates from persistent agent identities, Agent Office-owned task execution, model inference providers, optional external runtime integrations, and presentation-only state.

## Model providers and endpoints

**Model Provider**:
A hosted or local service that serves model inference, such as OpenAI or an Ollama server.
_Avoid_: Runtime Adapter

**Runtime Adapter**:
An optional integration that maps an external agent execution runtime's commands and evidence into Agent Office. It is not required for the MVP Agent Office Execution Engine and is distinct from a Provider Adapter.
_Avoid_: Model Provider

**Model Endpoint**:
The configured destination and model or route identifier used for inference through a Provider Adapter; it may point directly to a provider or to a gateway.
_Avoid_: Provider, when the upstream service is not known

**Provider Adapter**:
An integration that translates inference requests and responses to a provider API or endpoint protocol. It does not create or start Tasks/Runs, authorize tools, or emit Agent Office lifecycle state.
_Avoid_: Runtime Adapter

**Provider Router**:
A gateway that forwards or routes requests to one or more model providers, such as 9Router or OmniRoute. The actual upstream is known only when the router reports it.
_Avoid_: Model Provider, when referring to the gateway itself

**OpenAI-compatible endpoint**:
An endpoint that implements some OpenAI API request/response conventions; it is not necessarily operated by OpenAI, and supported API features may vary.
_Avoid_: OpenAI provider, unless OpenAI is the actual service

Provider adapters may use different protocols; no one protocol defines the Model Provider.

## Agent definitions

**Agent Instance**:
A persistent office identity created from an Agent Definition. It may remain idle without a Task, Run, or active execution; creating the instance does not start work. Project association identifies context, not access grants; physical office location belongs to the scene layout.
_Avoid_: Running agent, runtime session

**Agent Definition**:
A reusable role and system-prompt template with references to skills; it is distinct from an Agent Instance, Task, and Run.
_Avoid_: Running agent

**Agent Definition Version**:
An immutable revision of an Agent Definition's name, description, original Markdown, and unmapped metadata. A new edit creates a new version rather than changing a historical one.
_Avoid_: Current definition, when referring to a historical revision

**Archived Agent Definition**:
A definition retained for historical inspection but unavailable for new work. Archiving does not erase earlier versions or imply that existing work was stopped.
_Avoid_: Deleted definition
**Skill**:
Reusable instructions, resources, and declared dependencies that may be referenced by one or more Agent Definitions. Attaching a Skill does not grant permissions or prove runtime compatibility.
_Avoid_: Executable tool bundle, unless a separately approved and isolated execution mechanism exists

**Skill Version**:
An immutable revision of a Skill's instructions, text resources, and declared dependencies. Editing the Skill creates a new version rather than changing historical content.
_Avoid_: Current skill, when referring to a historical revision

**Pinned Skill Reference**:
A reference from one Agent Definition Version to an exact Skill Version. A pin preserves configuration identity; it does not mean that a run loaded or used the Skill.
_Avoid_: Installed skill, when describing a run's actual behavior



## Work and execution

**Agent Office Execution Engine**:
The P0 Agent Office-owned loop that starts explicitly assigned Tasks as Runs, enforces workspace/tool allowlists, requests inference through the configured Provider Adapter, and records canonical lifecycle and safe action events. A provider response alone does not establish work state; tool output, secrets, and hidden reasoning are not visual activity.
_Avoid_: Provider Adapter, Runtime Adapter

**Runtime Session**:
An execution context for one or more Run attempts; distinct from the persistent Agent Instance and the Task's workflow state.
_Avoid_: Agent Instance

**Delegation**:
An optional P1 relationship from a parent Run to a child Task/Run using a registered Agent Definition Version and bounded scope. It is not part of the initial independent-Task flow and is never inferred from prompt text or avatar movement.
_Avoid_: Child mention, agent name in prose

**Child Run**:
A temporary P1 Run created for an approved delegation and linked to its parent Run and Agent Definition Version. It remains in history after completion but does not create a permanent Agent Instance by default.
_Avoid_: Permanent Agent Instance

**Activity Event**:
A timestamped, attributable record of an Agent Office Engine/Runner lifecycle or tool event, or a user action. Provider inference output alone is not a work event; event availability and freshness determine whether current activity can be shown.
_Avoid_: Model response as run state

**Safe Tool-Action Summary**:
A concise user-visible label derived from an allowlisted tool event, such as “reading project file” or “running tests”. It excludes raw output, secrets, hidden reasoning, and data outside the authorized workspace.
_Avoid_: Chain of thought, complete command output


**Task**:
A user-defined unit of work with a project, goal, inputs, acceptance criteria, optional explicit agent assignment, and dependencies. Its workflow stage is distinct from runtime execution.
_Avoid_: Run

**Task Revision**:
An immutable version of a Task's work specification. Editing the Task creates a new revision while preserving earlier revisions.
_Avoid_: Run snapshot

**Run / Attempt**:
One execution attempt for a Task. A retry creates a new attempt; runtime outcome is distinct from Task acceptance.
_Avoid_: Task status

## Presentation data

**Visual Agent State**:
A presentation-only pose, animation, or destination derived from verified Task/Run events and the office scene. The initial visualization shows safe action summaries from real events; it cannot set work status or permissions, and stale evidence freezes work animation.
_Avoid_: Run state, proof of model activity

**Sample Data**:
Local-only example records used to preview Agent Office screens before real domain/event data is connected. They are not canonical Agent Instances, Tasks, Runs, or runtime evidence and cannot initiate or authorize work.
_Avoid_: Live data, runtime evidence

