CREATE TABLE IF NOT EXISTS public.oauth_tokens (
    provider text NOT NULL,
    account_id text NOT NULL,
    owner text,
    tokens text NOT NULL,
    updated_at bigint NOT NULL,
    revision bigint NOT NULL,
    display_name text,
    PRIMARY KEY (provider, account_id)
);
CREATE TABLE IF NOT EXISTS public.settings (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS public.org_members (
    org_id text NOT NULL,
    email text NOT NULL,
    role text NOT NULL,
    federation_removal_pending_at bigint,
    PRIMARY KEY (org_id, email)
);
CREATE TABLE IF NOT EXISTS public.builder_pending (
    state_hash text PRIMARY KEY,
    session_hash text NOT NULL,
    organisation_id text NOT NULL,
    user_id text NOT NULL,
    expires_at bigint NOT NULL,
    data text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.builder_connections (
    organisation_id text PRIMARY KEY,
    owner_email text NOT NULL,
    owner_id text NOT NULL,
    model text NOT NULL,
    connected_at bigint NOT NULL,
    verified_at bigint
);
CREATE TABLE IF NOT EXISTS public.builder_usage (
    organisation_id text NOT NULL,
    day text NOT NULL,
    calls integer NOT NULL CHECK (calls BETWEEN 1 AND 20),
    PRIMARY KEY (organisation_id, day)
);
CREATE TABLE IF NOT EXISTS public.identity_rekeys (
    id text PRIMARY KEY,
    old_email text NOT NULL,
    new_email text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    error text,
    actor_email text,
    counts_json text,
    created_at bigint NOT NULL,
    updated_at bigint NOT NULL,
    completed_at bigint
);
CREATE UNIQUE INDEX IF NOT EXISTS identity_rekeys_old_new_idx
    ON public.identity_rekeys (LOWER(old_email), LOWER(new_email));
