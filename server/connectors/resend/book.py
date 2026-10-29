from contracts import PlaybookDraft

from connectors.resend.config import definition


def procedure(domain_id: str, domain_name: str) -> PlaybookDraft:
    definition(domain_id)
    page = {"url_pattern": "https://resend.com/api-keys*"}

    def field(name: str) -> dict[str, str]:
        return {"kind": "field", "value": name}

    def role(value: str, name: str) -> dict[str, str]:
        return {"kind": "role", "value": value, "name": name}

    name = "${replacement_provider_display_name}"
    base = {"parameters": {}, "evidence_checks": ["page_verified"], "checkpoint": page}

    def step(
        identity: str, tool: str, operation: str, objective: str, **extra: object
    ) -> dict[str, object]:
        return {
            **base,
            **(
                {"checkpoint": {"url_pattern": "https://resend.com/api-keys/${old_provider_id}"}}
                if extra.get("stage") == "revoke"
                else {}
            ),
            "id": identity,
            "stage": "create",
            "tool": "browser." + tool,
            "operation": operation,
            "objective": objective,
            **extra,
        }

    return PlaybookDraft.model_validate(
        {
            "name": "Resend · " + domain_name,
            "platform": "resend",
            "allowed_domains": ["resend.com"],
            "login_url_pattern": "https://resend.com/login*",
            "verifier": {
                "mode": "resend-receipt",
                "base_url": "https://api.resend.com",
                "auth": {"scheme": "bearer"},
                "supports_overlap": True,
                "credential_ids": {
                    "kind": "css",
                    "value": 'table a[href^="/api-keys/"]:has(:text-matches(".+"))',
                },
                "credential_ids_attribute": "href",
                "credential_id_path": "/api-keys/",
                "operation": {
                    "method": "GET",
                    "path": "/inboxes",
                    "query": {"limit": "1"},
                    "success_statuses": [200],
                },
            },
            "steps": [
                step(
                    "list_keys",
                    "navigate",
                    "navigate",
                    "Open API keys",
                    parameters={"url": "https://resend.com/api-keys"},
                    checkpoint={**page, "required_text": ["API keys"]},
                ),
                step(
                    "open_create",
                    "click",
                    "open",
                    "Open key creation",
                    selectors=[role("button", "Create API key")],
                ),
                step(
                    "name_key",
                    "type",
                    "fill",
                    "Name the replacement key",
                    selectors=[role("textbox", "Name")],
                    parameters={"value": name},
                ),
                step(
                    "limit_permission",
                    "select",
                    "select",
                    "Select sending access",
                    selectors=[field("Permission")],
                    parameters={"value": "Sending access"},
                ),
                step(
                    "limit_domain",
                    "select",
                    "select",
                    "Select the allowed domain",
                    selectors=[field("Domain")],
                    parameters={"value": domain_name},
                ),
                step(
                    "create_key",
                    "secure-capture",
                    "create",
                    "Create the scoped replacement key",
                    effect="create-credential",
                    selectors=[{"kind": "css", "value": '[role="dialog"] button[type="submit"]'}],
                    conditions=[
                        {
                            "selector": role("textbox", "Name"),
                            "attribute": "value",
                            "expected": name,
                        },
                        {"selector": field("Permission"), "expected": "Sending access"},
                        {"selector": field("Domain"), "expected": domain_name},
                    ],
                    secure_field={
                        "name": "key",
                        "selector": {"kind": "css", "value": 'input[value^="re_"]'},
                        "provider_id_selector": {
                            "kind": "css",
                            "value": 'table a[href^="/api-keys/"]:has-text("' + name + '")',
                        },
                        "provider_id_attribute": "href",
                        "provider_id_path": "/api-keys/",
                        "provider_display_name_selector": {
                            "kind": "css",
                            "value": 'table a[href^="/api-keys/"]:has-text("' + name + '")',
                        },
                    },
                ),
                step(
                    "open_old",
                    "navigate",
                    "navigate",
                    "Open the selected old key",
                    stage="revoke",
                    parameters={"url": "https://resend.com/api-keys/${old_provider_id}"},
                    checkpoint={"url_pattern": "https://resend.com/api-keys/${old_provider_id}"},
                ),
                step(
                    "old_menu",
                    "click",
                    "open",
                    "Open the selected key menu",
                    stage="revoke",
                    selectors=[role("button", "More actions")],
                    conditions=[
                        {
                            "selector": role("heading", "${old_provider_display_name}"),
                            "expected": "${old_provider_display_name}",
                        }
                    ],
                ),
                step(
                    "open_retirement",
                    "click",
                    "open",
                    "Open key retirement confirmation",
                    stage="revoke",
                    selectors=[role("menuitem", "Delete API key")],
                ),
                step(
                    "confirm_old",
                    "type",
                    "fill",
                    "Confirm the selected old key name",
                    stage="revoke",
                    selectors=[{"kind": "css", "value": 'input[placeholder="Enter API Key name"]'}],
                    parameters={"value": "${old_provider_display_name}"},
                ),
                step(
                    "retire_key",
                    "revokeCredential",
                    "revoke",
                    "Retire the verified old key",
                    stage="revoke",
                    effect="revoke-credential",
                    selectors=[{"kind": "css", "value": '[role="dialog"] button[type="submit"]'}],
                    result_checkpoint={**page, "required_text": ["API keys"]},
                    conditions=[
                        {
                            "selector": {
                                "kind": "css",
                                "value": 'input[placeholder="Enter API Key name"]',
                            },
                            "attribute": "value",
                            "expected": "${old_provider_display_name}",
                        }
                    ],
                ),
            ],
        }
    )
