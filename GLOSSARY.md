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

## Presentation data

**Sample Data**:
Local-only example records used to preview Agent Office screens before real domain/event data is connected. They are not canonical Agent Instances, Tasks, Runs, or runtime evidence and cannot initiate or authorize work.
_Avoid_: Live data, runtime evidence

