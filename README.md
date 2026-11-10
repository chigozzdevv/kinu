# Kinu

Kinu rotates API keys when they are exposed, nearing expiry, or drifting. It creates replacements
through provider APIs or isolated browser sessions, deploys and verifies them, then retires the old
key.

## How rotation works

1. An alert, schedule, or operator starts a rotation.
2. Agent-Native proposes a plan within the credential’s controls and published Playbook.
3. Kinu creates and stores a replacement key, then deploys it to connected workloads.
4. Kinu verifies and observes the replacement. If required, an administrator approves revocation.
5. Kinu retires the old key and verifies that the provider rejects it.

If a provider or deployment has an uncertain outcome, Kinu holds the rotation and checks saved
evidence against the live provider and workload before recovery.

### How Kinu uses Agent-Native

For rotation planning, Kinu sends Agent-Native structured context such as the trigger, execution
method, connected app count, and approval settings. The planning request contains no key values.
Kinu validates the proposed stages against its workflow; the executor handles provider and browser
actions.

The [Agent-Native client](server/agents/native.py) sends the planning input through Kinu’s private
service:

```python
result = await self.client.call(
    "/_internal/rotation-plan",
    {"organisation_id": organisation_id, "input": value},
    timeout=60,
)
```

The executor wires `plan_rotation` and `assess` into rotation in
[server/executor/runtime.py](server/executor/runtime.py). The context and plan validation are in
[server/rotation/stages.py](server/rotation/stages.py).

## Self-host Kinu

Kinu runs as a Docker Compose stack. Install Docker Compose and Python 3.12 on the host.

1. Clone this repository and generate the local configuration:

   ```sh
   python3 -m deploy.setup
   ```

2. Set `KINU_RESEND_KEY` and `KINU_EMAIL_SENDER` in `.env` using a verified sender domain.

3. Build and start Kinu:

   ```sh
   make image
   docker compose up -d --no-build
   ```

4. Create the first administrator. Run this on the Kinu host, open the private link it prints, and
   verify the account’s email. The first account becomes the installation administrator.

   ```sh
   python3 -m deploy.setup --link
   ```

The dashboard is at `http://localhost:8080` by default.

### Public installation

Point a domain at the host and set these values in `.env`:

```dotenv
KINU_ENVIRONMENT=production
KINU_PUBLIC_URL=https://kinu.example.com
KINU_SITE=kinu.example.com
KINU_HTTP_PORT=80
KINU_HTTPS_PORT=443
```

Caddy obtains and renews the TLS certificate. Expose ports 80 and 443; keep MongoDB, Redis, and
application services on private container networks.

If another project already owns the host’s public ports, use `deploy/vps.yaml` with the existing
HTTPS proxy and the external `kinu-edge` network.

### Integrations

Connect workspace accounts in **Connections** and Builder AI in **Settings → AI**. Integrations
that need installation credentials or webhooks are listed in [`.env.example`](.env.example); set
their callback URLs on the public API domain.

### Backups and updates

Back up `.env`, `.secrets/`, and Docker volumes. `docker compose down` preserves data;
`docker compose down --volumes` deletes it. To update:

```sh
git pull
make image
docker compose up -d --no-build
```

Keep existing encryption keys. Changing one without migrating its encrypted records makes those
records unreadable.

## Workload health check

Each managed workload needs an HTTPS health URL. It must check the loaded key with its issuer, echo
Kinu’s `X-Kinu-Probe` value as `nonce`, and return the SHA-256 fingerprint of each managed variable.
Never return the key itself; disable response caching.

```json
{"ok":true,"nonce":"VALUE_OF_X_KINU_PROBE","fingerprints":{"API_KEY":"SHA256_OF_LOADED_KEY"}}
```

If the issuer rejects a key, return `{"ok":false,"state":"provider-rejected"}` with the same proof
fields. Register every workload that uses the credential.

## Development

Python 3.12 and Node.js 24 are required. Install dependencies and run the project checks with:

```sh
make sync
make verify
```
