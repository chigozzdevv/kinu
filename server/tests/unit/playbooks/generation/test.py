import json

import pytest
from connectors.resend.book import procedure
from contracts import PlaybookDraft
from core.errors import PlaybookError
from core.playbook.generation import validate_generated


def test_generated_procedures_use_the_supported_capture_and_revocation_contract() -> None:
    validate_generated(procedure("0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c", "mail.example"))


@pytest.mark.parametrize(
    "invalid", ["verifier", "overlap", "variable", "redacted", "static-revoke", "tool"]
)
def test_unusable_model_output_cannot_be_saved_as_a_generated_procedure(invalid: str) -> None:
    value = procedure("0b0ca8e1-fd3f-4edf-b762-88b01a1a6a9c", "mail.example").model_dump(
        mode="json"
    )
    if invalid == "verifier":
        value["verifier"] = None
    elif invalid == "overlap":
        value["verifier"]["supports_overlap"] = False
    elif invalid in {"variable", "redacted"}:
        value["steps"][2]["parameters"]["value"] = (
            "${target_scopes}" if invalid == "variable" else "[REDACTED]"
        )
    elif invalid == "static-revoke":
        value = json.loads(json.dumps(value).replace("${old_provider_id}", "fixed-key"))
    else:
        value["steps"][1]["tool"] = "browser.screenshot"
    with pytest.raises(PlaybookError):
        validate_generated(PlaybookDraft.model_validate(value))
