import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from connectors.github import GitHubWebhook
from contracts import HttpOperation, HttpProviderApi
from pydantic import ValidationError
from testkit import make_http_provider_api

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_http_provider_contract_rejects_non_origin_base_urls_and_unsafe_paths() -> None:
    template = make_http_provider_api()
    with pytest.raises(ValidationError, match="without credentials or query data"):
        HttpProviderApi(
            **{**template.model_dump(), "base_url": "https://api.sendgrid.com/v3?tenant=attacker"}
        )
    with pytest.raises(ValidationError, match="origin-relative paths"):
        HttpOperation(method="GET", path="//attacker.example/keys", success_statuses=(200,))
    with pytest.raises(ValidationError, match="invalid placeholder"):
        HttpOperation(method="GET", path="/keys/{provider-id}", success_statuses=(200,))
    with pytest.raises(ValidationError, match="unsupported fields"):
        HttpOperation(
            method="GET",
            path="/keys",
            success_statuses=(200,),
            metadata_fields={"api_key": "apiKey"},
        )


def test_github_webhook_rejects_modified_payload() -> None:
    webhook = GitHubWebhook()
    body = b'{"action":"created"}'
    signature = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    webhook.verify(body, signature, b"hook-secret")
    with pytest.raises(ValueError, match="invalid"):
        webhook.verify(body + b" ", signature, b"hook-secret")


def test_github_retry_is_stable_but_reopened_occurrence_is_distinct() -> None:
    webhook = GitHubWebhook()
    payload: dict[str, Any] = {
        "action": "created",
        "alert": {
            "number": 7,
            "html_url": "https://github.com/example/mailer/security/secret-scanning/7",
            "secret_type_display_name": "SendGrid API Key",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        },
        "repository": {"full_name": "example/mailer"},
    }
    first = webhook.normalise("org_one", "secret_scanning_alert", json.dumps(payload).encode(), NOW)
    retry = webhook.normalise("org_one", "secret_scanning_alert", json.dumps(payload).encode(), NOW)
    payload["action"] = "reopened"
    alert = payload["alert"]
    assert isinstance(alert, dict)
    alert["updated_at"] = NOW.replace(minute=1).isoformat()
    reopened = webhook.normalise(
        "org_one", "secret_scanning_alert", json.dumps(payload).encode(), NOW
    )
    assert first.id == retry.id
    assert first.id != reopened.id
