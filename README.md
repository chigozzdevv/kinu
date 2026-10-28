# Kinu

Credential rotation with policy checks, private execution and an audit trail.

Kinu runs through Docker Compose. MongoDB stores application data, Redis and BullMQ run
background jobs, and email/password authentication uses opaque cookie sessions. Agent-Native
shares the dashboard's identity and workspace permissions. Resend delivers verification and
password-reset emails.

## Run

Install Docker with Compose, then:

```sh
python3 -m deploy.setup
```

Edit `.env`: set `KINU_RESEND_KEY` and `KINU_EMAIL_SENDER` using a domain verified in Resend.

```sh
docker compose up -d --build
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

`deploy.setup` creates random database passwords and separate encryption/service keys without
overwriting existing values. Keep `.env`, `.secrets/` and database volumes in your backups.
Existing database passwords must be changed in MongoDB as well as the configuration. Changing
an encryption key without migrating its encrypted records makes those records unreadable.

Update with `git pull` followed by `docker compose up -d --build`. Index migrations run before
application services start. `docker compose down` preserves data; adding `--volumes` deletes it.

## Connections

Open **Settings → Integrations** to configure the OAuth apps used by this installation. These
settings require the verified installation administrator. Workspace administrators then connect
their own accounts through **Connections**. OAuth client secrets and account tokens are encrypted
in the private executor database; the dashboard receives configuration status only.

Gmail uses Google's read-only OAuth scope. Add exact alert senders and select their credential
providers after connecting. Kinu reads new matching messages every five minutes, ignores
attachments, and never sends mailbox bodies to the model. Automatic exposure handling requires
sender-aligned DKIM, a unique credential match and permitted controls. Register Google's web OAuth
client with the callback shown in Settings. Public Gmail use requires Google's verification;
Testing applications require test users and have limited refresh-token lifetimes.

Custom API providers accept their management token once. The executor keeps it encrypted and
reuses that connection across credentials. HTTP operation mappings must declare overlapping keys,
credential metadata and an authenticated credential test.

Vercel, Railway and Render workload selection and rotation are under development. Their settings
do not imply that a workload is ready to rotate. AWS adapters remain optional; the current fully
implemented workload executor uses one selected Lambda consumer and Secrets Manager resource.
Cloud role setup is separate from deploying Kinu.

## AI and execution

Connect Builder.io under **Settings → AI**. Each workspace authorizes its own Builder account
and chooses a model. OAuth requests AI invocation and refresh access. The default limit is 20
calls per workspace per UTC day; Builder's credits and account limits still apply.

The model assesses a small structured incident summary. It receives no workload keys, provider
management tokens or mailbox bodies. Policy checks decide which operations can run. The private
executor handles secrets and performs provider mutations; Agent-Native has access only to its
own encrypted grant database. Generic chat, terminals and MCP proxy routes are disabled.

Rotation preserves the old key during deployment and observation. Retirement requires current
verification evidence. An interrupted mutation is held for evidence review rather than replayed
blindly. The Chromium smoke test verifies capture barriers, private transfer and context disposal.
A general browser rotation worker is not yet live.

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
`docker compose exec -T executor python < deploy/browser.py` for the fake-key capture check. The Compose deployment has separate API, executor, worker, agent and web image targets.
