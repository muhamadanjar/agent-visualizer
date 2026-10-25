CREATE TABLE runs (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    task_id uuid NOT NULL,
    task_revision integer NOT NULL CHECK (task_revision >= 1),
    attempt_number integer NOT NULL CHECK (attempt_number >= 1),
    request_id uuid NOT NULL,
    definition_id uuid NOT NULL,
    definition_version integer NOT NULL CHECK (definition_version >= 1),
    status text NOT NULL DEFAULT 'created' CHECK (status = 'created'),
    configuration_snapshot jsonb NOT NULL CHECK (
        jsonb_typeof(configuration_snapshot) = 'object'
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, id),
    UNIQUE (owner_id, request_id),
    UNIQUE (owner_id, task_id, attempt_number),
    FOREIGN KEY (owner_id, task_id, task_revision)
        REFERENCES task_revisions (owner_id, task_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, definition_id, definition_version)
        REFERENCES agent_definition_versions (owner_id, definition_id, version) ON DELETE RESTRICT
);

CREATE INDEX runs_owner_task_attempt_idx
    ON runs (owner_id, task_id, attempt_number DESC);
