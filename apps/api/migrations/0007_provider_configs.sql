CREATE TABLE provider_configs (
    owner_id text PRIMARY KEY CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    adapter text NOT NULL CHECK (
        adapter = 'openai_compatible_chat_completions'
    ),
    endpoint_url text NOT NULL CHECK (
        char_length(endpoint_url) BETWEEN 1 AND 2048
    ),
    model_id text NOT NULL CHECK (
        char_length(model_id) BETWEEN 1 AND 200
    ),
    route text CHECK (
        route IS NULL OR char_length(route) BETWEEN 1 AND 120
    ),
    updated_at timestamptz NOT NULL DEFAULT now()
);
