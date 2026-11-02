# Kinu

Credential rotation with policy checks, private execution and an audit trail.

Kinu runs through Docker Compose. MongoDB stores application data, Redis and BullMQ run
background jobs, and email/password authentication uses opaque cookie sessions. Agent-Native
shares the dashboard's identity and workspace permissions. Resend delivers account, invitation
and notification emails.

## Run

Install Docker with Compose, then:

```sh
python3 -m deploy.setup
```

Edit `.env`: set `KINU_RESEND_KEY` and `KINU_EMAIL_SENDER` using a domain verified in Resend.

```sh
make image
docker compose up -d --no-build
```

Run `python3 -m deploy.setup --link` on the installation host. Open its private setup link,
create your account and verify your email. Setup grants that account the installation administrator
role in MongoDB. The link can only initialize the installation once. Regular sign-ups receive no
installation privileges. The dashboard is at <http://localhost:8080>.

For a public installation, point a domain at the host and set:

```dotenv
KINU_ENVIRONMENT=production
KINU_PUBLIC_URL=https://kinu.example.com
KINU_SITE=kinu.example.com
KINU_HTTP_PORT=80
KINU_HTTPS_PORT=443
```

Caddy issues the HTTPS certificate. Only Caddy's ports need public access. MongoDB, Redis,
the API, Agent-Native and the executor stay on container networks.
The API trusts forwarded client addresses from private container networks so sign-in limits apply
to each client. Set `KINU_TRUSTED_PROXIES` to your proxy addresses or CIDRs if you change that
network layout; keep the API port private.

`deploy.setup` creates random database passwords and separate encryption/service keys without
overwriting existing values. Keep `.env`, `.secrets/` and database volumes in your backups.
Existing database passwords must be changed in MongoDB as well as the configuration. Changing
an encryption key without migrating its encrypted records makes those records unreadable.

Update with `git pull`, `make image`, then `docker compose up -d --no-build`. Index migrations run before
application services start. `docker compose down` preserves data; adding `--volumes` deletes it.

Invitations and notification emails use the same Resend settings as sign-in. The worker retries
queued messages with a stable provider idempotency key and rechecks recipient access before sending.
Receipts distinguish provider acceptance from delivery. Receipt lookup requires a Resend key with
read access; sending-only keys can still send email.

## Connections

Open **Settings → Integrations** to configure the OAuth apps used by this installation. These
settings require the verified installation administrator. Workspace administrators then connect
their own accounts through **Connections**. OAuth client secrets and account tokens are encrypted
in the private executor database; the dashboard receives configuration status only.

Gmail uses Google's read-only OAuth scope. Add exact alert senders and select their credential
providers after connecting. Kinu reads new matching messages every five minutes, ignores
attachments, and never sends mailbox bodies to the model. Automatic exposure handling requires
sender-aligned DKIM using SHA-256 with an RSA key of at least 1024 bits, a unique credential match
and permitted controls. Senders should use 2048-bit keys where available.
Invalid signatures require manual review; temporary DNS failures are retried. Register Google's web OAuth
client with the callback shown in Settings. Public Gmail use requires Google's verification;
Testing applications require test users and have limited refresh-token lifetimes.

Custom API providers accept their management token once. The executor keeps it encrypted and
reuses that connection across credentials. HTTP operation mappings must declare overlapping keys,
credential metadata and an authenticated credential test.

Resend has a built-in provider connection. Supply a full-access management token and choose a
verified sending domain. Workload keys stay sending-only and scoped to that domain. Resend cannot
identify an existing key from its token, so Kinu only manages keys whose creation it recorded.
Creation receipts bind the issuer ID, scope and token fingerprint privately; unknown keys cannot
be imported or retired through this connection.

For Resend browser execution, open the provider connection and choose **Use browser**, then sign
in through its private browser. Edit an existing credential and select that browser connection.
Kinu preserves its generation, vault value and consumer bindings; an active rotation blocks the
switch. The linked API connection verifies receipts and issuer metadata. Creation and retirement
run through the dashboard with checked names, permissions, domain and key IDs. Kinu records the task
and pinned playbook version before inference. The agent receives that step's approved controls and
constraints; its public action explanation streams into the run history with the selected button.
Redacted screenshots before protected actions and after verified private key capture are encrypted
in the executor vault for 90 days and available to workspace members who can read the run. The result image is taken with
page scripts frozen and sensitive fields covered; secret values and screenshots never reach the model.

In **Credentials → Add credential**, choose a hosting account or connect Vercel, Railway or Render.
Select the project, its key variables and their issuer, then review the rotation controls. Kinu creates inventory and
consumer bindings automatically and reuses the existing connections across keys. Render uses an
API key; Vercel and Railway use installation-owned OAuth apps configured in Settings. Register the
exact callback shown there. Vercel needs project, environment-variable and deployment permissions;
Railway requests project membership and refresh access. No live provider registration is performed
by the application setup command.

For a first managed key, select a readable workload variable and an API provider, then choose
**Create managed key**. Supply the workload health URL and, for custom providers, the intended
key scopes. Kinu creates the key through that provider, captures it privately, installs it, deploys
the workload and verifies the loaded value before registering its inventory. This flow accepts
customer project and variable names. Resend's domain and sending-only rules remain in its adapter.
An existing provider key is left active for its owner to review. Browser providers currently require
an existing managed key before switching execution to the browser.

Key setup status stays beside the selected variable. **Check and recover** resumes a captured key
or restores the previous value after a failed deployment. A pending deployment keeps its key;
an unknown creation, variable write or deployment outcome stays held for account review.
Retries never blindly repeat those mutations.

Selected keys use Kinu's encrypted private vault. Rotation updates all registered hosting consumers
before retiring the predecessor. AWS remains optional and uses its separate Lambda/Secrets Manager
adapter, currently limited to one Lambda consumer per credential. Cloud role setup is separate from
deploying Kinu. Mixing Lambda and hosting consumers within one rotation is not supported.
To enable AWS setup in Compose, set `KINU_CONNECTION_PRINCIPALS` to the comma-separated broker
principal ARNs authorized to assume the managed connection roles, then recreate the API container.

A workload health URL is required before automated rotation. It must call the issuer using the loaded
key and return fresh proof. Kinu sends a random `X-Kinu-Probe` header; the response must echo it and
include a SHA-256 fingerprint of each selected variable's runtime value:

```json
{"ok": true, "nonce": "VALUE_OF_X_KINU_PROBE", "fingerprints": {"API_KEY": "SHA256_OF_LOADED_KEY"}}
```

After issuer rejection, return `ok: false` and `state: "provider-rejected"` with the same proof fields.
Do not return raw keys. Disable response caching. Merely returning a fingerprint without checking
the issuer does not meet this health contract. Every workload using the key must be registered.

Vercel variables must target production only. Preview variables, branch overrides, shared variables,
and custom environments are excluded. If the predecessor's value is unreadable, its immutable deployment URL
must support the retirement probe. Its health response must also include
`"provider_ids": {"API_KEY": "ISSUER_REPORTED_KEY_ID"}`, verified through the issuer. Rotation
stops if that identity cannot be proved. A failed rollout can restore traffic while leaving the project
variable on the replacement; Kinu then keeps both keys and holds the run for recovery. It does not
claim a completed rollback or revoke a key whose configuration may still be in use.

The worker checks expiry and drift every five minutes and queues rotations allowed by each
credential's controls. Credentials with an active rotation or key setup are excluded from monitoring
until that work finishes. Gmail sender changes preserve the polling cursor and pending messages.
The API readiness endpoint and worker health check require a recent worker heartbeat.

Selecting **Before revocation** pauses a rotation after deployment and observation. Another enabled
workspace administrator must approve it; a manual rotation cannot approve itself. The worker then
repeats observation and checks the bound action before retirement. Rejection restores the predecessor
when safe. Expired approvals and requests for more evidence produce a fresh approval after rechecking.
Approval requests expire after 24 hours; both keys remain active while waiting.

Workspace administrators can open a cleanup-required rotation and choose **Recover rotation**.
Recovery checks the saved evidence and live provider and workload state before restoring the
predecessor or finishing registration of an already verified retirement. It releases locks only
after verifying a safe outcome. Unresolved mutations stay held; expired provider sessions can be
reconnected while the cleanup lock is retained. Active execution renews its lease throughout long
deployments and observation. Rotation history supports pagination and direct links to older runs.

## AI and execution

Connect Builder.io under **Settings → AI**. Each workspace authorizes its own Builder account
and chooses a model. OAuth requests AI invocation and refresh access. The default limit is 100
calls per workspace per UTC day. An operator can grant a workspace a higher allowance for that
day without resetting its recorded call count. Builder's credits and account limits still apply.

The model assesses a small structured incident summary. It receives no workload keys, provider
management tokens or mailbox bodies. Policy checks decide which operations can run. The private
executor handles secrets and performs provider mutations; Agent-Native has access only to its
own encrypted grant database. Generic chat, terminals and MCP proxy routes are disabled.

Rotation preserves the old key during deployment and observation. Retirement requires current
verification evidence. An interrupted mutation is held for evidence review rather than replayed
blindly. The Chromium smoke test verifies capture barriers, private transfer and context disposal.
Create a playbook from instructions and public documentation links under **Playbooks → Add playbook**,
using the workspace's Builder account and selected model. Review the generated draft, then publish it.
Existing JSON definitions can also be imported.
Definitions declare exact public hostnames, checked page controls, private capture fields and a
fixed read-only issuer identity probe or a supported private receipt verifier. Sign in through the
connection's private browser once.
Stagehand routes sanitized control choices through the workspace's Builder account; Playwright
executes approved actions privately. The model never observes the provider page, screenshots or
login state. Creation closes that browser context after encrypted capture. Each protected step
can use several AI calls within the shared daily limit.

Browser rotation currently requires a readable predecessor, overlapping issuer keys and registered
hosting consumers. Procedures require review before publishing. Expired provider sessions require
reconnecting the connection.

## Development

Python 3.12 and Node.js 24 are required outside Docker.

```sh
make sync
make verify
```

The storage suite starts an isolated MongoDB replica set. Set `KINU_TEST_REDIS_URL` to isolated
Redis database 15 to include queue integration tests. No cloud account or model call is needed for
these checks. Run `python3 -m deploy.check` against a running Compose installation to check routing,
database isolation and encrypted vault persistence. Run
`docker compose exec -T executor python < deploy/browser.py` for private capture and Stagehand
checks with fake keys and mocked inference. The Compose deployment has separate API, executor,
worker, agent and web image targets.

The dedicated Render sandbox is defined in `examples/render/render.yaml`. It runs `kinu-check` on
Render's Free plan, with `RESEND_API_KEY=setup-required` until initialization. Connect Render and
Resend in Kinu, select this service, then run the following on the installation host using the
workspace, connection, service and administrator IDs from that installation:

```sh
docker compose exec -T executor python -m hosting.seed \
  --workspace WORKSPACE_ID --host RENDER_CONNECTION_ID --provider RESEND_CONNECTION_ID \
  --project RENDER_SERVICE_ID --url https://YOUR_SERVICE.onrender.com/probe --actor ADMIN_ACTOR_ID
```

This setup command accepts only the dedicated `kinu-check` service and its unchanged placeholder.
It creates one domain-scoped sending key, stores it privately, deploys it and registers its inventory
mapping. It never prints the key or sends email. An interrupted attempt requires evidence review;
rerunning it cannot create another key. A completed attempt returns its existing credential.
The workload's `/health` endpoint checks liveness; `/probe` verifies the loaded key with Resend.

If setup stopped before installation, `--recover` checks the unchanged placeholder and a private
creation receipt before retiring the unused key. It archives the attempt and clears its staging
secret. Setup can then be run again. Recovery refuses installed keys and unknown creation outcomes.
Use `--resume` to recheck an already captured key and continue installation without issuing another.
If its recorded deployment is already live, resumption finishes registration without redeploying.
