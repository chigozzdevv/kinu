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

CREATE TABLE IF NOT EXISTS kinu.auth_forms (
    token_hash text PRIMARY KEY,
    csrf_token text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_forms_expiry ON kinu.auth_forms(expires_at);
CREATE TABLE IF NOT EXISTS kinu.auth_limits (
    key text NOT NULL,
    bucket bigint NOT NULL,
    attempts integer NOT NULL CHECK (attempts>0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (key,bucket)
);
CREATE INDEX IF NOT EXISTS auth_limits_expiry ON kinu.auth_limits(expires_at);

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
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_source_event
    ON kinu.inventory (organisation_id, (data->>'source'), (data->>'source_event_id'))
    WHERE kind = 'ingestion-event';
CREATE UNIQUE INDEX IF NOT EXISTS incident_event
    ON kinu.inventory (organisation_id, (data->>'event_id')) WHERE kind = 'incident';

CREATE TABLE IF NOT EXISTS kinu.runs (
    organisation_id text NOT NULL REFERENCES kinu.organisations(id),
    id text NOT NULL,
    credential_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 0),
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, id),
    CHECK (data->>'organisation_id' = organisation_id AND data->>'id' = id),
    CHECK ((data->>'revision')::integer = revision)
);
CREATE TABLE IF NOT EXISTS kinu.run_locks (
    organisation_id text NOT NULL,
    credential_id text NOT NULL,
    run_id text NOT NULL,
    PRIMARY KEY (organisation_id, credential_id),
    FOREIGN KEY (organisation_id, run_id) REFERENCES kinu.runs(organisation_id, id)
);
CREATE TABLE IF NOT EXISTS kinu.commands (
    organisation_id text NOT NULL,
    id text NOT NULL,
    run_id text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, id),
    FOREIGN KEY (organisation_id, run_id) REFERENCES kinu.runs(organisation_id, id)
);
CREATE TABLE IF NOT EXISTS kinu.triggers (
    organisation_id text NOT NULL,
    source text NOT NULL,
    event_id text NOT NULL,
    command_id text NOT NULL,
    PRIMARY KEY (organisation_id, source, event_id),
    FOREIGN KEY (organisation_id, command_id) REFERENCES kinu.commands(organisation_id, id)
);
CREATE TABLE IF NOT EXISTS kinu.outbox (
    organisation_id text NOT NULL,
    id text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, id),
    FOREIGN KEY (organisation_id, id) REFERENCES kinu.commands(organisation_id, id)
);
CREATE TABLE IF NOT EXISTS kinu.audit (
    organisation_id text NOT NULL REFERENCES kinu.organisations(id),
    id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence >= 0),
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, id),
    UNIQUE (organisation_id, sequence),
    CHECK (data->>'organisation_id' = organisation_id AND data->>'id' = id),
    CHECK ((data->>'sequence')::bigint = sequence)
);
CREATE TABLE IF NOT EXISTS kinu.executions (
    organisation_id text NOT NULL,
    run_id text NOT NULL,
    stage text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (organisation_id, run_id, stage),
    FOREIGN KEY (organisation_id, run_id) REFERENCES kinu.runs(organisation_id, id),
    CHECK (data->>'organisation_id' = organisation_id AND data->>'run_id' = run_id),
    CHECK (data->>'stage' = stage)
);
CREATE TABLE IF NOT EXISTS kinu.mail (
    organisation_id text NOT NULL REFERENCES kinu.organisations(id),
    id text NOT NULL,
    data jsonb NOT NULL,
    done boolean NOT NULL DEFAULT false,
    assessment jsonb,
    PRIMARY KEY (organisation_id, id),
    CHECK (data->>'organisation_id' = organisation_id AND data->>'id' = id)
);
