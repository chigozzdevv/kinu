from telemetry import REDACTED, redact


def test_redact_removes_nested_secret_values() -> None:
    value = {
        "provider": "sendgrid",
        "api_key": "plaintext",
        "secret_reference": "secret://example/mail/versions/2",
        "payload": b"plaintext bytes",
        "nested": [{"authorization": "Bearer plaintext"}],
    }
    assert redact(value) == {
        "provider": "sendgrid",
        "api_key": REDACTED,
        "secret_reference": "secret://example/mail/versions/2",
        "payload": REDACTED,
        "nested": [{"authorization": REDACTED}],
    }


def test_redact_handles_camel_case_and_secret_bearing_values() -> None:
    value = {
        "apiKey": "marker-one",
        "accessToken": "marker-two",
        "safe": "Bearer abcdefghijklmnop",
        "redirect": "https://vendor.example/callback?code=sensitive",
        "secretReference": "secret://example/mail/versions/2",
    }
    assert redact(value) == {
        "apiKey": REDACTED,
        "accessToken": REDACTED,
        "safe": REDACTED,
        "redirect": REDACTED,
        "secretReference": "secret://example/mail/versions/2",
    }


def test_redactor_protects_oauth_state_code_and_pkce_verifier() -> None:
    assert redact(
        {"state": "short-lived-state", "code": "one-time-code", "pkceVerifier": "one-time-verifier"}
    ) == {"state": REDACTED, "code": REDACTED, "pkceVerifier": REDACTED}
