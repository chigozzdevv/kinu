from datetime import UTC, datetime

from agents.redact import redact

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def test_agent_context_recursively_redacts_secret_material() -> None:
    value = redact(
        {
            "name": "production",
            "secret_value": "must-not-escape",
            "credential_id": "credential_one",
            "secret_reference": "secret://test/mail/versions/2",
            "nested": {"authorization": "Bearer credential", "safe": 3},
        }
    )
    assert value == {
        "name": "production",
        "secret_value": "[REDACTED]",
        "credential_id": "credential_one",
        "secret_reference": "secret://test/mail/versions/2",
        "nested": {"authorization": "[REDACTED]", "safe": 3},
    }
