import json
from datetime import UTC, datetime

import httpx
import pytest
from agents.playbooks import PlaybookBuilder
from api.app import create_app
from api.deps import ApiServices
from connectors.resend.book import procedure
from contracts import PlaybookState
from core.account import AccountService
from core.account.store import AccountStore
from core.auth import AccessControl, AuthenticatedIdentity
from core.playbook import PlaybookService, WalkthroughService
from core.playbook.links import Links
from core.playbook.sources import Sources
from core.playbook.store import PlaybookStore
from core.private import PrivateClient
from core.storage.database import Database
from executor.app import app as executor
from executor.config import ExecutorSettings
from executor.runtime import Runtime
from executor.vault import Vault
from jobs.queue import Jobs
from pydantic import SecretStr
from tests.browser import HEADERS, make_browser

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("invalid", [False, True])
async def test_text_and_links_generate_a_reviewable_draft_through_the_native_builder(
    database: Database, identity: AuthenticatedIdentity, invalid: bool
) -> None:
    now = datetime.now(UTC)
    accounts = AccountStore(database, lambda: now)
    membership = await AccountService(accounts, lambda: now).create_organisation(
        identity, "Playbooks"
    )
    org = membership.organisation.id
    definition = procedure("0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c", "mail.example")
    calls: list[dict[str, object]] = []

    def native(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/_internal/playbooks"
        assert request.headers["authorization"] == "Bearer native-fixture"
        body = json.loads(request.content)
        calls.append(body)
        assert body["organisation_id"] == org
        source = json.dumps(body["input"]["sources"])
        assert "fixture-password" not in source and "fixture-api-secret" not in source
        assert "Create API key" in source and "Sending access" in source
        assert body["input"]["schema"]["properties"]["steps"]
        generated = definition.model_dump(mode="json")
        if invalid:
            generated["verifier"] = None
        return httpx.Response(
            200,
            json={
                "succeeded": True,
                "output": {"playbook_draft": generated},
                "completed_at": now.isoformat(),
            },
        )

    def document(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://docs.example.com/resend"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""
            <h1>Resend keys</h1><p>Choose Sending access for mail.example.</p>
            <p>api_key=fixture-api-secret</p><script>Never send scripts to the model.</script>
        """,
        )

    queue = Jobs("redis://localhost:6379")
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(native)) as native_http,
        httpx.AsyncClient(transport=httpx.MockTransport(document)) as docs,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=executor), base_url="http://executor"
        ) as private_http,
    ):
        config = ExecutorSettings(
            vault_key=SecretStr("vault-generation-test-at-least-thirty-two"),
            executor_token=SecretStr("executor-fixture"),
            native_token=SecretStr("native-fixture"),
        )
        assert config.vault_key and config.executor_token
        vault = Vault(database, config.vault_key)
        await vault.initialize()
        executor.state.service = Runtime(config, database, vault, native_http, queue)
        private = PrivateClient(private_http, "http://executor", config.executor_token)
        app = create_app(
            ApiServices(
                access=AccessControl(accounts),
                playbooks=PlaybookService(PlaybookStore(database), lambda: now),
                walkthroughs=WalkthroughService(Sources(database), lambda: now, Links(docs)),
                playbook_builder=PlaybookBuilder(private),
            ),
            browser=make_browser(identity),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            root = f"/v1/organisations/{org}/playbooks/playbook_generated"
            features = await http.get(f"/v1/organisations/{org}/features", headers=HEADERS)
            assert features.json()["playbook_generation"]
            for body in (
                {
                    "source_id": "source_text",
                    "kind": "text",
                    "content": 'Click Create API key. password="fixture-password".',
                },
                {
                    "source_id": "source_link",
                    "kind": "link",
                    "resource_url": "https://docs.example.com/resend",
                },
            ):
                response = await http.post(
                    root + "/walkthroughs/references", headers=HEADERS, json=body
                )
                assert response.status_code == 201, response.text
                assert all(
                    secret not in response.text
                    for secret in ("fixture-password", "fixture-api-secret")
                )
            response = await http.post(
                root + "/draft",
                headers=HEADERS | {"Idempotency-Key": "generation-one"},
                json={
                    "objective": "Build the Resend rotation procedure",
                    "source_ids": ["source_text", "source_link"],
                },
            )
            assert len(calls) == 1
            if invalid:
                assert response.status_code == 422 and "verification" in response.json()["message"]
                assert not await PlaybookStore(database).list_playbooks(org, 100)
            else:
                assert response.status_code == 200, response.text
                assert not await PlaybookStore(database).list_playbooks(org, 100)
                saved = await http.post(
                    root + "/versions",
                    headers=HEADERS,
                    json={
                        "version_id": "version_generated",
                        "definition": response.json()["definition"],
                        "source_ids": ["source_text", "source_link"],
                    },
                )
                assert saved.status_code == 201, saved.text
                assert saved.json()["version"]["state"] == PlaybookState.DRAFT
                published = await http.post(
                    root + "/versions/version_generated/publish", headers=HEADERS, json={}
                )
                assert (
                    published.status_code == 200
                    and published.json()["state"] == PlaybookState.PUBLISHED
                )
            foreign = await http.get(
                "/v1/organisations/org_other/playbooks/playbook_generated/walkthroughs/source_text",
                headers=HEADERS,
            )
            assert foreign.status_code == 403
            assert (
                await http.post(
                    root + "/walkthroughs/references",
                    headers=HEADERS,
                    json={
                        "source_id": "video_one",
                        "kind": "video",
                        "resource_url": "https://docs.example.com/video.mp4",
                    },
                )
            ).status_code == 422
            assert (
                await http.post(root + "/walkthroughs", headers=HEADERS, json={"size": 10})
            ).status_code in {404, 405}
        records = await database.collection("playbook_sources").find({}).to_list()
        assert "fixture-password" not in str(records) and "fixture-api-secret" not in str(records)
    await queue.close()
