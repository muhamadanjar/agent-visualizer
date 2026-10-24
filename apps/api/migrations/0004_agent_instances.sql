ALTER TABLE projects
    ADD CONSTRAINT projects_owner_id_key UNIQUE (owner_id, id);

CREATE TABLE agent_instances (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    display_name text NOT NULL CHECK (
        char_length(display_name) BETWEEN 1 AND 120 AND display_name = btrim(display_name)
    ),
    definition_id uuid NOT NULL,
    project_id uuid,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, id),
    FOREIGN KEY (owner_id, definition_id)
        REFERENCES agent_definitions (owner_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, project_id)
        REFERENCES projects (owner_id, id) ON DELETE RESTRICT
);

CREATE INDEX agent_instances_owner_created_idx
    ON agent_instances (owner_id, created_at, id);
CREATE INDEX agent_instances_owner_project_idx
    ON agent_instances (owner_id, project_id, created_at, id)
    WHERE project_id IS NOT NULL;
