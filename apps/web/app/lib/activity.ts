export type ActivityEvent = {
  event_id: string;
  event_version: number;
  sequence: number;
  timestamp: string;
  type: string;
  owner_id: string;
  project_id: string;
  agent_id: string;
  run_id: string;
  task_id: string;
  payload: {
    task_revision: number;
    source_sequence: number | null;
    safe_action_summary: string | null;
    approval_id?: string | null;
  };
};

export type RuntimeProjection = {
  run_id: string;
  task_id: string;
  project_id: string;
  agent_instance_id: string;
  status: string;
  freshness: "fresh" | "stale";
  last_event_cursor: number;
  last_event_type: string;
  last_event_at: string;
  last_heartbeat_at: string | null;
  safe_action_summary: string | null;
};

export type ActivitySnapshot = {
  cursor: number;
  projections: RuntimeProjection[];
};

export type AgentAvailabilityState = "idle" | "working" | "waiting" | "unknown";

export type AgentAvailability = {
  agent_instance_id: string;
  availability: AgentAvailabilityState;
};

export type RunActivity = {
  run_id: string;
  status: string | null;
  freshness: "unknown" | "fresh" | "stale";
  last_event_at: string | null;
  last_heartbeat_at: string | null;
  cursor: number;
  safe_action_summary: string | null;
  events: ActivityEvent[];
};

export type ActivityConnection = "connecting" | "syncing" | "connected" | "disconnected" | "unavailable";

export type ActivityMessage =
  | { type: "snapshot"; snapshot: ActivitySnapshot }
  | { type: "events"; events: ActivityEvent[] }
  | { type: "connection"; state: ActivityConnection };

export type ApprovalRecord = {
  id: string;
  run_id: string;
  action_type: string;
  action_digest: string;
  resource_ref: string | null;
  created_at: string;
  expires_at: string;
  decision: "approved" | "rejected" | "expired" | null;
  decided_at: string | null;
};

export type ArtifactMetadata = {
  id: string;
  run_id: string;
  artifact_type: string;
  display_name: string;
  media_type: string | null;
  byte_length: number | null;
  sha256: string | null;
  created_at: string;
  available: boolean;
};

async function requestActivity<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json() as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    throw new Error(typeof detail === "string"
      ? detail
      : `Could not load activity data (HTTP ${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export function requestActivitySnapshot(signal?: AbortSignal): Promise<ActivitySnapshot> {
  return requestActivity<ActivitySnapshot>("/api/v1/activity/snapshot", signal);
}

function isAgentAvailability(value: unknown): value is AgentAvailability {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.agent_instance_id === "string"
    && (record.availability === "idle"
      || record.availability === "working"
      || record.availability === "waiting"
      || record.availability === "unknown");
}

export async function requestAgentAvailability(
  instanceIds: string[],
  signal?: AbortSignal,
): Promise<AgentAvailability[]> {
  const records: AgentAvailability[] = [];
  for (let offset = 0; offset < instanceIds.length; offset += 200) {
    const query = new URLSearchParams();
    for (const instanceId of instanceIds.slice(offset, offset + 200)) {
      query.append("agent_instance_id", instanceId);
    }
    const value: unknown = await requestActivity<unknown>(
      `/api/v1/agent-instances/availability?${query.toString()}`,
      signal,
    );
    if (!Array.isArray(value) || !value.every(isAgentAvailability)) {
      throw new Error("The Agent Availability response was not recognized.");
    }
    records.push(...value);
  }
  return records;
}

export function requestRunActivity(runId: string, signal?: AbortSignal): Promise<RunActivity> {
  return requestActivity<RunActivity>(
    `/api/v1/runs/${encodeURIComponent(runId)}/activity`,
    signal,
  );
}

export function requestRunApprovals(runId: string, signal?: AbortSignal): Promise<ApprovalRecord[]> {
  return requestActivity<ApprovalRecord[]>(
    `/api/v1/runs/${encodeURIComponent(runId)}/approvals`,
    signal,
  );
}

export function requestRunArtifacts(runId: string, signal?: AbortSignal): Promise<ArtifactMetadata[]> {
  return requestActivity<ArtifactMetadata[]>(
    `/api/v1/runs/${encodeURIComponent(runId)}/artifacts`,
    signal,
  );
}

function isActivityEvent(value: unknown): value is ActivityEvent {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  if (
    typeof record.event_id !== "string"
    || typeof record.event_version !== "number"
    || typeof record.sequence !== "number"
    || typeof record.timestamp !== "string"
    || typeof record.type !== "string"
    || typeof record.owner_id !== "string"
    || typeof record.project_id !== "string"
    || typeof record.agent_id !== "string"
    || typeof record.run_id !== "string"
    || typeof record.task_id !== "string"
    || typeof record.payload !== "object"
    || record.payload === null
  ) return false;
  const payload = record.payload as Record<string, unknown>;
  return typeof payload.task_revision === "number"
    && (typeof payload.source_sequence === "number" || payload.source_sequence === null)
    && (typeof payload.safe_action_summary === "string" || payload.safe_action_summary === null)
    && (
      payload.approval_id === undefined
      || typeof payload.approval_id === "string"
      || payload.approval_id === null
    );
}

function isActivitySnapshot(value: unknown): value is ActivitySnapshot {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.cursor === "number"
    && Array.isArray(record.projections)
    && record.projections.every((projection) => {
      if (typeof projection !== "object" || projection === null) return false;
      const item = projection as Record<string, unknown>;
      return typeof item.run_id === "string"
        && typeof item.task_id === "string"
        && typeof item.project_id === "string"
        && typeof item.agent_instance_id === "string"
        && typeof item.status === "string"
        && (item.freshness === "fresh" || item.freshness === "stale")
        && typeof item.last_event_cursor === "number"
        && typeof item.last_event_type === "string"
        && typeof item.last_event_at === "string"
        && (typeof item.last_heartbeat_at === "string" || item.last_heartbeat_at === null)
        && (typeof item.safe_action_summary === "string" || item.safe_action_summary === null);
    });
}

export function subscribeToActivity(emit: (message: ActivityMessage) => void): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let pingTimer: ReturnType<typeof setInterval> | undefined;
  let stopped = false;
  let lastCursor = 0;

  function connect() {
    if (stopped) return;
    emit({ type: "connection", state: "connecting" });
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws`);
    socket.onmessage = (message) => {
      let value: unknown;
      try {
        value = JSON.parse(String(message.data));
      } catch {
        return;
      }
      if (typeof value !== "object" || value === null) return;
      const record = value as Record<string, unknown>;
      if (record.type === "session_ready") {
        emit({ type: "connection", state: lastCursor > 0 ? "syncing" : "connected" });
        const subscription = lastCursor > 0
          ? { type: "subscribe", after_cursor: lastCursor }
          : { type: "subscribe" };
        socket?.send(JSON.stringify(subscription));
        pingTimer = setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "ping" }));
          }
        }, 20_000);
      } else if (record.type === "activity_snapshot" && isActivitySnapshot(record)) {
        lastCursor = Math.max(lastCursor, record.cursor);
        emit({ type: "snapshot", snapshot: { cursor: record.cursor, projections: record.projections as RuntimeProjection[] } });
      } else if (
        (record.type === "activity_events" || record.type === "activity_replay")
        && Array.isArray(record.events)
      ) {
        if (record.type === "activity_replay") {
          emit({ type: "connection", state: "syncing" });
        }
        const events = record.events.filter(isActivityEvent);
        if (events.length !== record.events.length) return;
        const unseen: ActivityEvent[] = [];
        for (const event of events) {
          if (event.sequence <= lastCursor) continue;
          if (event.sequence !== lastCursor + 1) {
            socket?.close(4000, "activity cursor gap");
            return;
          }
          unseen.push(event);
          lastCursor = event.sequence;
        }
        if (unseen.length > 0) emit({ type: "events", events: unseen });
      } else if (record.type === "activity_replay_complete") {
        emit({ type: "connection", state: "connected" });
      } else if (record.type === "activity_unavailable") {
        emit({ type: "connection", state: "unavailable" });
      }
    };
    socket.onclose = (event) => {
      clearInterval(pingTimer);
      pingTimer = undefined;
      socket = null;
      if (stopped) return;
      const reconnect = () => {
        if (stopped) return;
        emit({ type: "connection", state: "disconnected" });
        reconnectTimer = setTimeout(connect, 2_000);
      };
      if (event.code === 1008) {
        emit({ type: "connection", state: "unavailable" });
        return;
      }
      if (event.code === 1006) {
        void fetch("/api/v1/auth/me", {
          cache: "no-store",
          credentials: "same-origin",
          headers: { accept: "application/json" },
        })
          .then((response) => {
            if (stopped) return;
            if (response.status === 401 || response.status === 403) {
              emit({ type: "connection", state: "unavailable" });
              return;
            }
            reconnect();
          })
          .catch(reconnect);
        return;
      }
      reconnect();
    };
    socket.onerror = () => socket?.close();
  }

  connect();
  return () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    clearInterval(pingTimer);
    socket?.close();
    socket = null;
  };
}
