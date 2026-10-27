CREATE SCHEMA IF NOT EXISTS kinu;

CREATE TABLE IF NOT EXISTS kinu.organisations (
    id text PRIMARY KEY,
    data jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS kinu.members (
    organisation_id text NOT NULL REFERENCES kinu.organisations(id),
    subject text NOT NULL,
    id text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, subject),
    UNIQUE (organisation_id, id)
);

CREATE TABLE IF NOT EXISTS kinu.invitations (
    organisation_id text NOT NULL REFERENCES kinu.organisations(id),
    id text NOT NULL,
    email text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, id)
);
CREATE INDEX IF NOT EXISTS invitations_email ON kinu.invitations(email);

CREATE TABLE IF NOT EXISTS kinu.sessions (
    token_hash text PRIMARY KEY,
    identity jsonb NOT NULL,
    csrf_token text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON kinu.sessions(expires_at);

CREATE TABLE IF NOT EXISTS kinu.logins (
    state_hash text PRIMARY KEY,
    browser_hash text NOT NULL,
    verifier text NOT NULL,
    nonce text NOT NULL,
    return_path text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS logins_expiry ON kinu.logins(expires_at);

CREATE TABLE IF NOT EXISTS kinu.inventory (
    organisation_id text NOT NULL REFERENCES kinu.organisations(id),
    kind text NOT NULL,
    id text NOT NULL,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, kind, id),
    CHECK ((data->>'organisation_id') IS NOT DISTINCT FROM organisation_id),
    CHECK ((data->>'id') IS NOT DISTINCT FROM id),
    CHECK (coalesce((data->>'revision')::integer, 0) = revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS inventory_control_number
    ON kinu.inventory (organisation_id, (data->>'credential_id'), (data->>'number'))
    WHERE kind = 'controls';
