from uuid import UUID

from contracts import HttpProviderApi


def definition(domain: str | None = None) -> HttpProviderApi:
    if domain is not None and str(UUID(domain)) != domain:
        raise ValueError("Choose a valid Resend domain")
    return HttpProviderApi.model_validate(
        {
            "base_url": "https://api.resend.com",
            "auth": {"scheme": "bearer"},
            "supports_overlap": True,
            "list_credentials": {
                "method": "GET",
                "path": "/api-keys",
                "success_statuses": [200],
                "list_items": "data",
                "provider_id_field": "id",
                "name_field": "name",
                "metadata_fields": {"created_at": "created_at", "last_used_at": "last_used_at"},
            },
            "create_credential": {
                "method": "POST",
                "path": "/api-keys",
                "success_statuses": [200, 201],
                "body": {
                    "name": "${name}",
                    "permission": "sending_access",
                    **({"domain_id": domain} if domain else {}),
                },
                "provider_id_field": "id",
                "secret_field": "token",
                "name_field": "name",
            },
            "revoke_credential": {
                "method": "DELETE",
                "path": "/api-keys/{provider_id}",
                "success_statuses": [200],
            },
            "test_credential": {
                "method": "GET",
                "path": "/inboxes",
                "query": {"limit": "1"},
                "success_statuses": [200],
            },
            "credential_auth": {"scheme": "bearer"},
        }
    )


def scopes(api: HttpProviderApi) -> list[str]:
    domain = api.create_credential.body.get("domain_id")
    return ["sending_access", *(["domain:" + domain] if isinstance(domain, str) else [])]
