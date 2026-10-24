CREATE TABLE tasks (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    current_revision integer NOT NULL CHECK (current_revision >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, id)
);

CREATE TABLE task_revisions (
    task_id uuid NOT NULL,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    revision integer NOT NULL CHECK (revision >= 1),
    project_id uuid NOT NULL,
    agent_instance_id uuid,
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200 AND title = btrim(title)),
    goal text NOT NULL CHECK (char_length(goal) BETWEEN 1 AND 10000 AND goal = btrim(goal)),
    input_text text NOT NULL CHECK (char_length(input_text) <= 10000),
    resource_refs text[] NOT NULL CHECK (cardinality(resource_refs) <= 100),
    acceptance_criteria text[] NOT NULL CHECK (
        cardinality(acceptance_criteria) BETWEEN 1 AND 100
    ),
    max_duration_minutes integer CHECK (max_duration_minutes BETWEEN 1 AND 1440),
    max_child_runs integer CHECK (max_child_runs BETWEEN 0 AND 100),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, revision),
    UNIQUE (owner_id, task_id, revision),
    FOREIGN KEY (owner_id, task_id) REFERENCES tasks (owner_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, project_id) REFERENCES projects (owner_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, agent_instance_id)
        REFERENCES agent_instances (owner_id, id) ON DELETE RESTRICT
);

ALTER TABLE tasks
    ADD CONSTRAINT tasks_current_revision_fk
    FOREIGN KEY (owner_id, id, current_revision)
    REFERENCES task_revisions (owner_id, task_id, revision)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX task_revisions_owner_project_created_idx
    ON task_revisions (owner_id, project_id, created_at DESC, task_id);

CREATE TABLE task_revision_dependencies (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    task_id uuid NOT NULL,
    revision integer NOT NULL,
    blocked_by_task_id uuid NOT NULL,
    PRIMARY KEY (task_id, revision, blocked_by_task_id),
    CHECK (task_id <> blocked_by_task_id),
    FOREIGN KEY (owner_id, task_id, revision)
        REFERENCES task_revisions (owner_id, task_id, revision) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, blocked_by_task_id)
        REFERENCES tasks (owner_id, id) ON DELETE RESTRICT
);

CREATE INDEX task_revision_dependencies_owner_blocked_idx
    ON task_revision_dependencies (owner_id, blocked_by_task_id, task_id);
