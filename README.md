# Kinu

Credential rotation with verified consumer updates, policy gates and an audit trail.

Kinu runs on AWS. Cognito provides browser sign-in; the API stores opaque cookie sessions,
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
administrators authorize Builder AI, select a model and verify the connection. OAuth requests
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
No `VITE_` value may contain a secret. Production requires HTTPS and matching Cognito callback
and sign-out URLs. Sessions expire within one hour; writes require Origin and CSRF checks.

```sh
make verify
```

Verification covers contracts, policy, state transitions, PostgreSQL persistence, concurrent
rotation delivery, rollback, email deduplication, session sharing, workspace isolation and CSRF.
Local Cognito tests use signed fixture tokens. A passing local suite does not prove AWS access
or Builder account authorization. The SDK's disabled chat dependency has a transitive Zod 3
advisory; review it before enabling any generic chat route.

Deployment instructions are in [infra/README.md](infra/README.md). Invitation delivery and
arbitrary production provider connections are not implemented.

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
