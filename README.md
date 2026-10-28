# Kinu

Credential rotation with verified consumer updates, policy gates and an audit trail.

Kinu runs on AWS. Kinu provides its own email/password forms backed by Cognito; the API stores opaque cookie sessions,
workspace permissions and inventory in PostgreSQL. Agent-Native shares that identity and
exposes the same scoped actions used by the dashboard.

## Current flow

1. An operator requests a rotation, or a labelled demo email reports an exposure.
2. Email events are deduplicated and correlated with the current credential. Builder AI
   assesses a small structured summary; application policy decides whether rotation is allowed.
3. An SQS worker validates the pinned controls and creates a replacement through the provider API.
4. The provider writes the key directly to AWS Secrets Manager and returns its reference.
5. Kinu verifies consumer access, publishes an immutable Lambda version and promotes its live alias.
6. After 60 seconds of healthy observations without old-key use, Kinu retires the old provider
   key, proves it is rejected and updates the inventory and audit history.

The deployed executor is limited to the isolated demo provider and consumer. It uses real
AWS resources and actual key validation. The email is an explicitly labelled MIME fixture;
it is not a connected inbox. Real mailbox OAuth and additional provider/runtime adapters
remain separate integrations. AgentCore Browser awaits an AWS quota approval; the browser
adapter and capture guards have local tests, but the browser rotation is not live verified.

Failures before retirement restore the previous consumer and revoke the candidate when safe.
Failures after retirement retain the working replacement and flag recovery for an operator.
A worker crash after mutation requires evidence review; Kinu does not blindly rerun it.

## Connect AI

Sign in, select a workspace and open **Settings → AI → Connect Builder.io**. Workspace
administrators authorize Builder AI and select a model. OAuth requests
only AI invocation and refresh access. Each workspace is capped at 20 inference calls per UTC day.
Builder account access and usage allowances still apply.

Builder grants are encrypted in a separate database. The model receives incident enums,
confidence, candidate count and a policy result. It receives no workload key, mailbox body,
AWS credentials or arbitrary execution tools. Generic Agent-Native chat, terminals, MCP proxy
routes and alternative authentication methods remain disabled.

The API and Agent-Native roles cannot assume customer roles. The worker uses a role restricted
to the selected demo resources; it cannot read workload secret values. Only the provider's
write path and consumer's read path handle those values. Inventory and execution evidence store
references, identifiers and checks.

## Development

Requirements: Python 3.12, uv, Node.js 24+ and npm. Local services are for development and
predeployment verification; AWS is the deployment target.

```sh
make sync
cp .env.example .env
cp agent/.env.example agent/.env
cp client/.env.example client/.env.local
make db
```

Configure Cognito and `KINU_DATABASE_URL` in the API environment, then run `make migrate`.
Start `make api`, `make agent` and `npm --prefix client run dev` in separate terminals.
Open http://localhost:5173. The local database binds to `127.0.0.1:5433`.

Keep root environment values out of the frontend and Agent-Native process. Local Agent-Native
storage uses its own `DATABASE_URL`; deployed storage uses a separate database and IAM role.
No `VITE_` value may contain a secret. Production requires HTTPS and a Cognito public app client with `ALLOW_USER_PASSWORD_AUTH`. Sessions expire within 12 hours; writes require Origin and CSRF checks.

```sh
make verify
```

Verification covers contracts, policy, state transitions, PostgreSQL persistence, concurrent
rotation delivery, rollback, email deduplication, session sharing, workspace isolation and CSRF.
Local Cognito tests use signed fixture tokens. A passing local suite does not prove AWS access
or Builder account authorization. The SDK's disabled chat dependency has a transitive Zod 3
advisory; review it before enabling any generic chat route.

Infrastructure templates and deployment utilities are in `infra/`. Invitation delivery and
arbitrary production provider connections are not implemented.

## Deployment

Run infrastructure commands from the repository root with an authorized temporary AWS session,
`AWS_REGION` and `PYTHONPATH=server:packages/contracts/src:packages/policy/src:packages/telemetry/src`.
Create the auth, store and artifact stacks from their matching templates before initialization.

```sh
python -m infra.bootstrap
python -m infra.package --api
python -m infra.deploy
python -m infra.native
python -m infra.bootstrap --native
python -m infra.launch
python -m infra.deploy
```

Database initialization is explicit. Routine app, native and demo deployments do not initialize
roles or schemas. Deployment tools discover stack outputs from AWS; they do not depend on local
JSON state. Build the frontend and upload `client/dist` to the app stack's private frontend bucket.
Retain older hashed assets for open tabs. Packaging installs locked Linux Python dependencies into
a fresh staging directory. Omit `--api` to include the remote Playwright driver.

`infra/check.py` runs storage tests against the deployed database. For a synthetic test login,
run `python -m infra.user --output /path/to/private/demo-user.json`; this command never resets
an existing demo account when its original credential file is missing. Demo launch uses an
explicit `KINU_DEMO_ORGANISATION`, an existing deployment, or the synthetic Cognito workspace.
Existing demo role bindings are recovered from PostgreSQL.

## Layout

| Path | Purpose |
| --- | --- |
| `client/` | Operator dashboard |
| `agent/` | Agent-Native actions and Builder AI connection |
| `server/api/`, `server/core/` | Auth, inventory, policy and workflow |
| `server/demo/`, `server/cloud/` | Scoped API executor, provider and consumer |
| `server/browser/`, `server/capture/` | Protected browser observations and capture |
| `server/connectors/` | AWS and provider adapters |
| `packages/` | Shared contracts, policy and telemetry |
| `infra/` | AWS templates, packaging and deployment |

Licensed under Apache 2.0. See [LICENSE](LICENSE).

Sign-up, email verification, code resend and password reset use the same Cognito pool. Passwords
and provider tokens stay out of Kinu storage. A password reset revokes existing Kinu sessions.
