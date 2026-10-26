# Kinu

Credential rotation and verification, with policy gates, approvals, and evidence.

Kinu includes the dashboard, domain services, and provider interfaces. AWS integrations
and the Agent-Native runtime are not implemented yet.

## What is retained

- React and TypeScript dashboard, landing page, routes, forms, and styling.
- Typed domain contracts, rotation state machine, policy gates, and capability validation.
- Portable services for inventory, approvals, incidents, accounts, notifications, and playbooks.
- HTTP provider and GitHub connectors behind a secret-store interface.
- Browser session controls, secure-capture logic, redaction, and telemetry interfaces.
- Applicable tests and a test-only in-memory run repository.

## Current runtime status

The frontend builds and the API can start without cloud credentials. Authentication and
backend services are unconfigured. Sign-in stays disabled, protected operations stay gated,
and `/health/ready` returns HTTP 503 until real services are supplied to `create_app`.
`/health/live` only confirms that the API process is running.

The next implementation work is AWS storage, secret management, identity, event delivery,
runtime provisioning, and Agent-Native integration. There are no deployment resources,
working AWS adapters, live service URLs, or configured credentials in this baseline.
The retained landing-page content is awaiting the planned UI review.

## Local development

Requirements: Python 3.12, uv, Node.js 24+, and npm.

```sh
make sync
make api
```

In another terminal:

```sh
cp client/.env.example client/.env.local
npm --prefix client run dev
```

Open http://127.0.0.1:5173. The local frontend proxies `/v1` and `/health` to
http://127.0.0.1:8000. Set `KINU_DEV_API_URL` to change the development proxy.
Public `VITE_` variables must never contain credentials.

```sh
make verify
```

This checks Python formatting, lint, types, tests, and the frontend lint/type/build.
`make image` builds the API container when Docker is available. No deployment is performed.

## Layout

| Path | Purpose |
| --- | --- |
| `client/` | Existing operator UI |
| `server/api/` | API routes and injectable service composition |
| `server/core/` | Portable business rules and service interfaces |
| `server/agents/` | Agent schemas, redaction, and runtime/repository interfaces |
| `server/broker/` | Capability validation and governed tool execution logic |
| `server/browser/`, `server/capture/` | Portable browser and capture controls |
| `server/connectors/` | Provider interfaces and retained HTTP/GitHub connectors |
| `packages/` | Contracts, policy, telemetry, and test utilities |
| `infra/` | Infrastructure implementation notes |
| `evidence/` | Evidence schema |

Licensed under Apache 2.0. See [LICENSE](LICENSE).
