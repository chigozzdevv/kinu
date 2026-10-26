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
