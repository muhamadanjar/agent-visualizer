CREATE TABLE projects (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    summary text NOT NULL DEFAULT '' CHECK (char_length(summary) <= 500),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX projects_owner_created_idx ON projects (owner_id, created_at, id);
