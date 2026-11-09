import json

import pytest
from connectors.resend.book import procedure
from contracts import PlaybookDraft
from core.errors import PlaybookError
from core.playbook.generation import validate_generated


def test_generated_procedures_use_the_supported_capture_and_revocation_contract() -> None:
    validate_generated(procedure("0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c", "mail.example"))


def test_generated_procedures_do_not_require_a_verifier() -> None:
    draft = procedure("0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c", "mail.example").model_copy(
        update={"verifier": None}
    )
    validate_generated(draft)


@pytest.mark.parametrize("invalid", ["variable", "redacted", "static-revoke", "tool"])
def test_unusable_model_output_cannot_be_saved_as_a_generated_procedure(invalid: str) -> None:
    value = procedure("0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c", "mail.example").model_dump(
        mode="json"
    )
    if invalid in {"variable", "redacted"}:
        value["steps"][2]["parameters"]["value"] = (
            "${target_scopes}" if invalid == "variable" else "[REDACTED]"
        )
    elif invalid == "static-revoke":
        value = json.loads(json.dumps(value).replace("${old_provider_id}", "fixed-key"))
    else:
        value["steps"][1]["tool"] = "browser.screenshot"
    with pytest.raises(PlaybookError):
        validate_generated(PlaybookDraft.model_validate(value))


@pytest.mark.parametrize(
    "change", ["missing-identity", "writable-identity", "read-only-create", "read-only-revoke"]
)
def test_api_playbooks_require_private_identity_checks_and_write_operations(change: str) -> None:
    from contracts.api_playbook import ApiPlaybookDraft
    from pydantic import ValidationError
    from testkit import make_http_provider_api

    api = make_http_provider_api().model_dump(mode="json")
    if change == "missing-identity":
        api["test_credential"] = None
        api["credential_auth"] = None
    elif change == "writable-identity":
        api["test_credential"]["method"] = "POST"
    elif change == "read-only-create":
        api["create_credential"]["method"] = "GET"
    else:
        api["revoke_credential"]["method"] = "GET"
    with pytest.raises(ValidationError):
        ApiPlaybookDraft.model_validate({"name": "Example", "platform": "example", "http": api})
