# Runner boundary

The future runner owns an authenticated outbound control-plane connection, scoped workspace/process creation, Runtime Adapter supervision, resource and egress enforcement, and event spooling until durable acknowledgement. It must not inherit browser credentials or treat a transport write as runtime acceptance.

This directory records the Phase 2 process boundary only. No runtime has passed the complete Phase 1 safety gates, so there is no production runner implementation or runnable placeholder. Runner execution, workspace isolation, resource controls, and recovery belong to Phases 6–7; Compose intentionally does not start a runner.

Provider Adapter selection and model inference remain separate from the Runtime Adapter. Credentials stay outside the browser and are released only to the approved isolated execution path.
