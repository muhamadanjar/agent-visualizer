# Agent Office

This glossary distinguishes the model service an agent uses from the runtime that executes the agent and from gateways that route model requests.

## Model providers and endpoints

**Model Provider**:
A hosted or local service that serves model inference, such as OpenAI or an Ollama server.
_Avoid_: Runtime Adapter

**Runtime Adapter**:
An integration that maps application commands and runtime evidence to an agent execution runtime, such as OMP.
_Avoid_: Model Provider

**Model Endpoint**:
The configured destination and model or route identifier used for inference through a Provider Adapter; it may point directly to a provider or to a gateway.
_Avoid_: Provider, when the upstream service is not known

**Provider Adapter**:
An integration that translates the inference contract to a provider API or endpoint protocol. It is distinct from the Runtime Adapter.
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
A persistent agent identity linked to an Agent Definition and optionally assigned to a Project. It can exist without a Runtime Session. Project association identifies context, not access grants; physical office location belongs to the scene layout.
_Avoid_: Running agent, runtime session

**Agent Definition**:
A reusable role identity containing instructions and references to skills; it is distinct from an Agent Instance and Runtime Session.
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

**Sample Data**:
Local-only example records used to preview Agent Office screens before real domain/event data is connected. They are not canonical Agent Instances, Tasks, Runs, or runtime evidence and cannot initiate or authorize work.
_Avoid_: Live data, runtime evidence

