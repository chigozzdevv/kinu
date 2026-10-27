# Kinu

Credential rotation and verification, with policy gates, approvals, and evidence.

The app includes the operator dashboard, Cognito browser sign-in, PostgreSQL sessions,
workspace access and inventory, and an Agent-Native service that shares the authenticated identity.
Credential rotation, mailbox connections, AWS secret storage, and deployment are still pending.

## Local development

AWS is the deployment target. Local services support development and predeployment checks.

Requirements: Python 3.12, uv, Node.js 24+, and npm.

```sh
make sync
cp .env.example .env
cp agent/.env.example agent/.env
cp client/.env.example client/.env.local
make db
```

The development database persists in `.cache/db` and listens only on `127.0.0.1:5433`.
Use PostgreSQL or Amazon RDS for deployed environments. Set the API's `KINU_DATABASE_URL`
to that database with TLS and a dedicated application role.

Configure the Cognito values in the root `.env`, then initialize the schema:

```sh
make migrate
```

Run each service in its own terminal:

```sh
make api
make agent
npm --prefix client run dev
```

Open http://localhost:5173. Vite proxies `/auth/`, `/v1`, and `/health` to the API on
port 8000, and `/_agent-native` to the Agent-Native service on port 8001.
Keep the API environment separate: never load the root `.env` into the frontend or agent.
Neither service needs database credentials. Public `VITE_` variables must never hold secrets.

## Sign-in and sessions

Create a Cognito user pool and a **public app client without a client secret**. Enable the
authorization code grant and `openid`, `email`, and `profile` scopes. Configure a managed
login domain, require email verification, and register these exact local URLs:

| Setting | URL |
| --- | --- |
| Callback | `http://localhost:5173/auth/callback` |
| Sign-out | `http://localhost:5173/auth` |

Set `KINU_COGNITO_POOL_ID`, `KINU_COGNITO_CLIENT_ID`, and `KINU_COGNITO_DOMAIN` in the API
environment. The domain includes `https://`. Production also requires
`KINU_ENVIRONMENT=production` and an HTTPS `KINU_PUBLIC_URL` with matching Cognito URLs.

The API exchanges the authorization code using PKCE and verifies signed Cognito ID tokens,
issuer, audience, expiry, nonce, and verified email. Single-use login state is tied to the
initiating browser. Cognito access and refresh tokens are not retained or sent to browser JavaScript.

The browser receives an opaque HttpOnly, SameSite=Lax session cookie. Production uses a
Secure `__Host-kinu` cookie. PostgreSQL stores its SHA-256 digest, identity, CSRF token,
and expiry. Sessions last at most one hour and never outlive the verified ID token.
After expiry, users sign in again through Cognito. Unsafe API requests require a matching
Origin and CSRF token. Logout deletes the server session before redirecting to Cognito sign-out.

Workspace memberships, profiles, and invitations persist in PostgreSQL. Workspace access is
checked against current membership on each request. Invitation delivery is not implemented yet.

Connections, applications, environments, services, credentials, generations, bindings, and
control versions also persist in PostgreSQL. Imports and cascading archives are atomic;
edits require the current revision. Relationships and connection boundaries are checked
inside the write transaction. Secret values are not inventory fields; records hold references.
AWS connection validation and secret storage still require their service adapters.

## Agent-Native

`agent/` uses the Agent-Native custom authentication plugin. It resolves the Kinu cookie
through `/auth/session` and passes verified user and selected workspace metadata to the
framework. It forwards only the Kinu cookie and `X-Kinu-Workspace`; it does not return
session credentials, Cognito tokens, or CSRF tokens in the framework session response.
Agent requests require a selected workspace in `X-Kinu-Workspace`.

Only the authenticated `GET /_agent-native/auth/session` endpoint and process health endpoint
are enabled. Agent chat, generic tools, terminals, framework authentication alternatives,
and credential rotation are not enabled. Missing or revoked Kinu sessions are rejected
before the framework can try another authentication method.

The framework's disabled chat dependency still includes an affected Zod 3 release with
no patched Zod 3 release. `npm audit` reports that upstream chain; an AG-UI 1 override
breaks the current AgentKit exports. Recheck the dependency before enabling chat.

## Verification

```sh
make verify
```

This checks Python formatting, lint, types, tests, the frontend build, and the Agent-Native
build and session tests. It also starts an ephemeral PostgreSQL-compatible database for SQL
repository tests, then runs the built API and Agent-Native services together to verify
inventory persistence, identity sharing, workspace isolation, CSRF enforcement, expiry,
and logout revocation.
Login tests use a simulated Cognito provider with real signed tokens; they do not contact AWS.

`make image` builds the API container when Docker is available. Schema initialization is
explicit through `make migrate`; API startup does not modify the database schema.
`/health/live` confirms process health. API `/health/ready` requires browser authentication
and account services to be configured; it does not certify credential rotation readiness.
No AWS infrastructure has been deployed by this repository setup.

## Layout

| Path | Purpose |
| --- | --- |
| `client/` | React operator UI |
| `agent/` | Agent-Native service and shared session integration |
| `server/api/` | API routes and service composition |
| `server/core/` | Business rules, authentication, and PostgreSQL stores |
| `server/agents/`, `server/broker/` | Agent contracts and governed execution interfaces |
| `server/browser/`, `server/capture/` | Browser and capture controls |
| `server/connectors/` | Provider interfaces and HTTP/GitHub connectors |
| `packages/` | Contracts, policy, telemetry, and test utilities |
| `infra/` | Infrastructure notes |
| `evidence/` | Evidence schema |

Licensed under Apache 2.0. See [LICENSE](LICENSE).
