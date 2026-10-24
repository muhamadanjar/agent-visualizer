ALTER TABLE agent_definition_versions
    ADD COLUMN owner_id text;

UPDATE agent_definition_versions AS version
SET owner_id = definition.owner_id
FROM agent_definitions AS definition
WHERE definition.id = version.definition_id;

ALTER TABLE agent_definition_versions
    ALTER COLUMN owner_id SET NOT NULL,
    ADD CONSTRAINT agent_definition_versions_owner_check
        CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    ADD CONSTRAINT agent_definition_versions_owner_id_version_key
        UNIQUE (owner_id, definition_id, version),
    ADD CONSTRAINT agent_definition_versions_owner_definition_fk
        FOREIGN KEY (owner_id, definition_id)
        REFERENCES agent_definitions (owner_id, id) ON DELETE RESTRICT;

CREATE TABLE skills (
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

CREATE TABLE skill_versions (
    skill_id uuid NOT NULL,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    version integer NOT NULL CHECK (version > 0),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 500),
    instructions text NOT NULL CHECK (char_length(instructions) BETWEEN 1 AND 1048576),
    dependencies jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(dependencies) = 'array'),
    resources jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(resources) = 'array'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, version),
    UNIQUE (owner_id, skill_id, version),
    FOREIGN KEY (owner_id, skill_id) REFERENCES skills (owner_id, id) ON DELETE RESTRICT
);

ALTER TABLE skills
    ADD CONSTRAINT skills_current_version_fk
    FOREIGN KEY (owner_id, id, current_version)
    REFERENCES skill_versions (owner_id, skill_id, version)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE agent_definition_version_skills (
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    definition_id uuid NOT NULL,
    definition_version integer NOT NULL CHECK (definition_version > 0),
    skill_id uuid NOT NULL,
    skill_version integer NOT NULL CHECK (skill_version > 0),
    position smallint NOT NULL CHECK (position BETWEEN 0 AND 99),
    PRIMARY KEY (owner_id, definition_id, definition_version, skill_id),
    UNIQUE (owner_id, definition_id, definition_version, position),
    FOREIGN KEY (owner_id, definition_id, definition_version)
        REFERENCES agent_definition_versions (owner_id, definition_id, version)
        ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, skill_id, skill_version)
        REFERENCES skill_versions (owner_id, skill_id, version)
        ON DELETE RESTRICT
);

CREATE INDEX skills_owner_created_idx ON skills (owner_id, created_at, id);
CREATE INDEX agent_definition_version_skills_skill_idx
    ON agent_definition_version_skills (owner_id, skill_id, skill_version);
