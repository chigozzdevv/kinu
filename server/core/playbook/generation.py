import json
import re

from contracts import PlaybookDraft, Stage

from core.errors import PlaybookError
from core.playbook.service import validate_definition


def validate_generated(definition: PlaybookDraft) -> None:
    validate_definition(definition)
    if definition.verifier is None or not definition.verifier.supports_overlap:
        raise PlaybookError(
            "Add the issuer's read-only credential verification details to the instructions"
        )
    allowed = {
        "browser.navigate",
        "browser.type",
        "browser.select",
        "browser.click",
        "browser.secure-capture",
        "browser.revokeCredential",
    }
    if any(step.tool not in allowed for step in definition.steps):
        raise PlaybookError("The generated procedure contains an unsupported browser action")
    serialized = definition.model_dump_json()
    placeholders = set(re.findall(r"\$\{([^{}]+)\}", serialized))
    if not placeholders <= {
        "replacement_provider_display_name",
        "old_provider_id",
        "old_provider_path",
        "old_provider_display_name",
    }:
        raise PlaybookError(
            "The generated procedure uses an unknown key variable. Generate it again."
        )
    if "[REDACTED]" in serialized or any("*" in domain for domain in definition.allowed_domains):
        raise PlaybookError(
            "Add the exact public hostnames and control details to the instructions"
        )
    revocation = json.dumps(
        [step.model_dump(mode="json") for step in definition.steps if step.stage is Stage.REVOKE]
    )
    if not any(value in revocation for value in ("${old_provider_id}", "${old_provider_path}")):
        raise PlaybookError("The generated revocation must identify the key being rotated")
