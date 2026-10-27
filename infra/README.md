# Kinu infrastructure

Deploy in `eu-west-2` using an authorized temporary AWS CLI session. No static deployment
access keys are required. Keep stack outputs and local deployment files outside Git.

| Stack | Resources |
| --- | --- |
| `kinu-auth` | Cognito Lite pool and public password-auth client |
| `kinu-store` | Private Aurora PostgreSQL, Data API and separate database login secrets |
| `kinu-artifacts` | Private encrypted deployment bucket with a 14-day artifact expiry |
| `kinu-app` | HTTP API Gateway, Python Lambda and private frontend bucket |
| `kinu-native` | Agent-Native HTTP Lambda, private inference Lambda and encrypted OAuth storage |
| `kinu-demo` | SQS worker, demo provider, DynamoDB key hashes, workload secret and Lambda consumer |

There is no NAT gateway or CloudFront dependency. Aurora is limited to 1 ACU and pauses after
five idle minutes. Lambda and API Gateway are usage based; the database, Secrets Manager and
other resources can still consume credits. Creating these stacks is not a promise of zero cost.

## Deploy

Install the locked development dependencies with `make sync`. Set `AWS_PROFILE`, `AWS_REGION`
and optionally `KINU_AWS_CLI` to the approved CLI executable. Python infrastructure scripts
need `PYTHONPATH=server:packages/contracts/src:packages/policy/src:packages/telemetry/src`.

Create `kinu-auth`, `kinu-store` and `kinu-artifacts` from their matching YAML templates using
CloudFormation. Initialize application tables and the restricted database role:

```sh
python infra/bootstrap.py
```

Prepare Linux x86_64 Python 3.12 dependencies in `.build/lambda` before packaging. Export
production server dependencies with the `aws` and `browser` extras; install binary Linux
wheels rather than copying the Mac virtual environment. Use `--api` for the API and demo
workers, which do not run browser sessions. Omit it when packaging a browser worker to include
the Playwright remote driver. Neither package includes local Chromium browser binaries.

```sh
python infra/package.py --api
python infra/deploy.py
python infra/native.py
```

`deploy.py` saves the HTTPS origin to `/private/tmp/kinu-aws/app.json`. Upload `client/dist`
after `npm --prefix client run build` to the private frontend bucket returned by `kinu-app`.
Keep old hashed assets for existing tabs. Sign-in uses Kinu's own forms and the Cognito user
pools API; a hosted sign-in domain and OAuth callback URLs are not required.

`native.py` builds the Node Lambda and private inference bundle, initializes `kinu_native`
and grants it access only to that database. Builder OAuth tokens and pending authorization
state are encrypted using a seal fetched from Secrets Manager at runtime.

## Isolated API demo

`launch.py` creates and verifies `kinu-demo`, seeds explicit demo inventory and checks the
consumer with its real workload role. Set `KINU_DEMO_ORGANISATION` for a chosen workspace.
The first launch otherwise selects the sole Cognito-backed workspace, or uses an existing
synthetic test account through `workspace.py`. A synthetic fixture can be created explicitly
with `user.py`; its generated password stays in a mode-600 temporary file.

```sh
python infra/launch.py
python infra/deploy.py
```

The second app deployment connects the existing SQS queue to authenticated rotation and demo
email actions. Re-running `launch.py` preserves an existing credential and live consumer alias.
Repackage and redeploy the app and demo stacks after changing Python source.

Sign in, connect Builder under **Settings → AI** and use the labelled demo email action on
Incidents to exercise AI assessment. Manual rotation exercises the same API executor without
requiring inference. The fixture does not send or retrieve real email.

The provider alone writes new workload keys; the consumer alone reads them. The worker sees
references, uses a resource-scoped role with an external ID and workspace tag, and cannot call
`GetSecretValue` on the workload secret. API and Agent-Native roles cannot assume that role.

Execution checkpoints, generation updates and audit records persist in PostgreSQL. Retry
and crash boundaries are explicit: a completed job is idempotent, pre-retirement failures
attempt safe rollback, and interrupted mutations can require operator recovery.

`browser.yaml` defines a custom AgentCore Browser with raw recording disabled. Deploy it
only after AWS grants the required configuration and session quotas. Browser execution is
not a prerequisite for the API demo.

## Account forms

Kinu handles sign-in, sign-up, email confirmation, code resend and password reset through its
own API. Enable `ALLOW_USER_PASSWORD_AUTH` on the public Cognito client. The API validates
Cognito's signed ID token, requires verified email and creates the existing opaque session.
Passwords and Cognito tokens are not persisted in Kinu or returned to browser JavaScript.

Pre-authentication forms use a browser-bound CSRF token with a ten-minute lifetime. PostgreSQL
limits attempts by hashed email and client address. A successful sign-in consumes its form;
a successful password reset revokes the account's Kinu sessions across devices. Cognito sends
verification and reset codes through its configured email service. Local tests exercise signed
provider responses; verify actual email delivery using an address you control.
