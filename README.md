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
4. The private executor stores the key in the selected AWS Secrets Manager secret and returns a version reference.
5. Kinu verifies consumer access, publishes an immutable Lambda version and promotes its live alias.
6. After the configured healthy observation window, Kinu retires the old provider key, proves it
   is rejected and updates inventory and audit history. The isolated demo also checks old-key usage.

The API executor supports a declared HTTP credential provider, Secrets Manager and one selected
Lambda consumer. The isolated demo remains available with its own provider and consumer.
Its email is an explicitly labelled MIME fixture, not a connected inbox. Gmail OAuth is not
implemented yet. AgentCore Browser awaits an AWS quota approval; the browser
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

The API and Agent-Native roles cannot assume customer roles. Private connection checks and
rotation functions assume a workspace-specific role using an external ID and session tag.
Session policies restrict each operation to the selected resources. Only these private functions
and the consumer handle secret values; the dashboard, model and execution history receive
references, identifiers and checks.

## Available features

The default runtime persists playbooks, probe definitions, approval decisions, agent records,
notifications, overview counts and rotation history in PostgreSQL. Approval capabilities require
matching saved verification evidence and a separate approver, and can be consumed only once.
The API executors reject controls requiring manual revocation approval. The Lambda HTTP adapter
also rejects generation-telemetry requirements until a telemetry adapter is connected.

The authenticated features endpoint reports deployed integration availability. AWS onboarding and
HTTP provider setup require the private connection function. GitHub onboarding, browser sessions
and playbook generation remain disabled until their adapters are configured.

## Connect a workload

1. Open **Connections → AWS**. Select the account, region, exact secret ARNs and Lambda ARNs.
   Include customer KMS key ARNs when needed. Install the generated CloudFormation access role,
   then return to Kinu and connect. Pending connections can resume with **Finish setup**.
2. Add a **Custom API** connection. Upload its operation mappings and select a separate management
   credential version from Secrets Manager. List metadata must include ID, name, kind, scopes and
   active/disabled status; the credential test must return its ID. Declare concurrent key support.
3. Add a credential and select its current secret version, provider identity and Lambda binding.
   Kinu checks those relationships before saving the mapping.

The Lambda must have a published `live` alias and an environment variable containing the full
immutable reference `arn:aws:secretsmanager:REGION:ACCOUNT:secret:NAME-SUFFIX/versions/VERSION`.
Kinu updates only that binding, preserves other environment variables and checks alias revisions.
Route production invocations through `live`; other aliases and unqualified invocations are not managed.

Implement a health operation accepting `{"kinu":{"operation":"health","challenge":"VALUE"}}`.
An optional `secret_reference` inside `kinu` asks the consumer to test a staged version of the same
secret. Restrict overrides to that secret. Read the version with the consumer's AWS role and make a
real, harmless provider request. Return only:

```json
{"kinu":{"challenge":"VALUE","healthy":true,"secret_reference":"FULL_VERSION_REFERENCE","provider_id":"KEY_ID"}}
```

For an authenticated rejection return `healthy: false` and `reason: "credential-rejected"` with the
same challenge. Network errors must not report credential rejection. Never return the key.
The current adapter supports one consumer, 60–600 seconds of observation and automatic revocation.
It does not claim application-wide traffic or old-key telemetry coverage.

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

Infrastructure templates and deployment utilities are in `infra/`. Invitation delivery is not implemented.

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
python -m infra.executor
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
| `server/rotation/`, `server/cloud/` | Shared staged executor and private AWS functions |
| `server/demo/` | Isolated demo provider, consumer and email fixture |
| `server/browser/`, `server/capture/` | Protected browser observations and capture |
| `server/connectors/` | AWS and provider adapters |
| `packages/` | Shared contracts, policy and telemetry |
| `infra/` | AWS templates, packaging and deployment |

Licensed under Apache 2.0. See [LICENSE](LICENSE).

Sign-up, email verification, code resend and password reset use the same Cognito pool. Passwords
and provider tokens stay out of Kinu storage. A password reset revokes existing Kinu sessions.
