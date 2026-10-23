CREATE TABLE agent_definitions (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    slug text NOT NULL CHECK (char_length(slug) BETWEEN 1 AND 120),
    current_version integer NOT NULL DEFAULT 1 CHECK (current_version > 0),
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, slug),
    UNIQUE (owner_id, id)
);

CREATE TABLE agent_definition_versions (
    definition_id uuid NOT NULL REFERENCES agent_definitions(id) ON DELETE RESTRICT,
    version integer NOT NULL CHECK (version > 0),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 500),
    source_markdown text NOT NULL CHECK (char_length(source_markdown) BETWEEN 1 AND 1048576),
    unmapped_metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(unmapped_metadata) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (definition_id, version)
);

ALTER TABLE agent_definitions
    ADD CONSTRAINT agent_definitions_current_version_fk
    FOREIGN KEY (id, current_version)
    REFERENCES agent_definition_versions (definition_id, version)
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX agent_definitions_owner_created_idx
    ON agent_definitions (owner_id, created_at, id);
