ALTER TABLE task_revisions
    ADD CONSTRAINT task_revisions_owner_task_revision_project_key
    UNIQUE (owner_id, task_id, revision, project_id);

ALTER TABLE runs
    ADD CONSTRAINT runs_owner_run_task_revision_key
    UNIQUE (owner_id, id, task_id, task_revision);

CREATE TABLE owner_event_cursors (
    owner_id text PRIMARY KEY CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    cursor bigint NOT NULL DEFAULT 0 CHECK (cursor >= 0)
);

CREATE TABLE activity_events (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    cursor bigint NOT NULL CHECK (cursor >= 1),
    source_id text NOT NULL CHECK (char_length(source_id) BETWEEN 1 AND 128),
    event_id uuid NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version = 1),
    source_session_id uuid,
    workspace_id uuid,
    project_id uuid NOT NULL,
    task_id uuid NOT NULL,
    task_revision integer NOT NULL CHECK (task_revision >= 1),
    agent_instance_id uuid NOT NULL,
    run_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type IN (
        'run.started', 'run.waiting_input', 'run.waiting_approval', 'run.blocked',
        'run.succeeded', 'run.failed', 'run.canceled', 'session.heartbeat'
    )),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    source_sequence bigint CHECK (source_sequence IS NULL OR source_sequence >= 0),
    PRIMARY KEY (owner_id, cursor),
    UNIQUE (owner_id, source_id, event_id),
    FOREIGN KEY (owner_id, run_id, task_id, task_revision)
        REFERENCES runs (owner_id, id, task_id, task_revision) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, task_id, task_revision, project_id)
        REFERENCES task_revisions (owner_id, task_id, revision, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, agent_instance_id)
        REFERENCES agent_instances (owner_id, id) ON DELETE RESTRICT
);

CREATE INDEX activity_events_owner_run_cursor_idx
    ON activity_events (owner_id, run_id, cursor);
CREATE INDEX activity_events_owner_project_cursor_idx
    ON activity_events (owner_id, project_id, cursor);

CREATE TABLE run_activity_projections (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    run_id uuid NOT NULL,
    task_id uuid NOT NULL,
    task_revision integer NOT NULL CHECK (task_revision >= 1),
    project_id uuid NOT NULL,
    source_id text NOT NULL CHECK (char_length(source_id) BETWEEN 1 AND 128),
    agent_instance_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN (
        'running', 'waiting_input', 'waiting_approval', 'blocked',
        'succeeded', 'failed', 'canceled'
    )),
    last_event_cursor bigint NOT NULL CHECK (last_event_cursor >= 1),
    last_event_type text NOT NULL,
    last_event_at timestamptz NOT NULL,
    last_sequence bigint CHECK (last_sequence IS NULL OR last_sequence >= 0),
    last_heartbeat_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, run_id),
    FOREIGN KEY (owner_id, run_id, task_id, task_revision)
        REFERENCES runs (owner_id, id, task_id, task_revision) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, task_id, task_revision, project_id)
        REFERENCES task_revisions (owner_id, task_id, revision, project_id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, agent_instance_id)
        REFERENCES agent_instances (owner_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, last_event_cursor)
        REFERENCES activity_events (owner_id, cursor) ON DELETE RESTRICT
);

CREATE INDEX run_activity_owner_project_status_idx
    ON run_activity_projections (owner_id, project_id, status, last_event_at DESC);
CREATE INDEX run_activity_owner_agent_status_idx
    ON run_activity_projections (owner_id, agent_instance_id, status, last_event_at DESC);

CREATE TABLE approval_requests (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    request_id uuid NOT NULL,
    action_type text NOT NULL CHECK (char_length(action_type) BETWEEN 1 AND 64),
    action_digest text NOT NULL CHECK (action_digest ~ '^[0-9a-f]{64}$'),
    resource_ref text CHECK (resource_ref IS NULL OR char_length(resource_ref) BETWEEN 1 AND 2048),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (owner_id, id),
    UNIQUE (owner_id, run_id, request_id),
    FOREIGN KEY (owner_id, run_id) REFERENCES runs (owner_id, id) ON DELETE RESTRICT,
    CHECK (expires_at > created_at)
);

CREATE INDEX approval_requests_owner_run_created_idx
    ON approval_requests (owner_id, run_id, created_at DESC);

CREATE TABLE approval_decisions (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    approval_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'expired')),
    decided_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, approval_id),
    FOREIGN KEY (owner_id, approval_id)
        REFERENCES approval_requests (owner_id, id) ON DELETE RESTRICT
);

CREATE TABLE artifacts (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    artifact_type text NOT NULL CHECK (char_length(artifact_type) BETWEEN 1 AND 64),
    display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 240),
    media_type text CHECK (media_type IS NULL OR char_length(media_type) BETWEEN 1 AND 120),
    byte_length bigint CHECK (byte_length IS NULL OR byte_length >= 0),
    sha256 text CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    storage_key uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, id),
    UNIQUE (owner_id, storage_key),
    FOREIGN KEY (owner_id, run_id) REFERENCES runs (owner_id, id) ON DELETE RESTRICT
);

CREATE INDEX artifacts_owner_run_created_idx
    ON artifacts (owner_id, run_id, created_at DESC);

CREATE TABLE office_scene_versions (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    scene_id text NOT NULL CHECK (char_length(scene_id) BETWEEN 1 AND 80),
    version integer NOT NULL CHECK (version >= 1),
    contents jsonb NOT NULL CHECK (jsonb_typeof(contents) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, scene_id, version)
);

CREATE INDEX office_scene_versions_owner_latest_idx
    ON office_scene_versions (owner_id, scene_id, version DESC);
