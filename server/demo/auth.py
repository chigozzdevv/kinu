import base64
import hashlib
import hmac
import json
import time


def signature(seed: bytes, purpose: str, value: str) -> str:
    return (
        base64.urlsafe_b64encode(
            hmac.new(seed, f"{purpose}\0{value}".encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )


def session(seed: bytes, organisation_id: str) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"org": organisation_id, "expires": int(time.time()) + 600}, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return payload + "." + signature(seed, "session", payload)


def identity(seed: bytes, cookie: str) -> str | None:
    payload, separator, signed = cookie.partition(".")
    if not separator or not hmac.compare_digest(signed, signature(seed, "session", payload)):
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(value["org"]) if int(value["expires"]) > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None
