# Dispatcher boundary

The future dispatcher owns claiming durable, ready-to-run commands under short leases and delivering them to authenticated runners. It must not hold database locks while a task runs, execute model/provider code, or make user-visible task state authoritative without runner acknowledgement.

This directory records the Phase 2 process boundary only. There is no queue/outbox contract or production dispatcher implementation yet; those depend on the task/run command model and belong to Phase 6. It is intentionally not a no-op service and is not started by Compose.

The dispatcher will consume a versioned control-plane command contract and send commands over the runner's authenticated outbound channel. It must not receive browser-originated traffic or expose a public port.
