import re
import secrets

_PREFIX = re.compile(r"^[a-z][a-z0-9]{1,15}$")


def new_id(prefix: str) -> str:
    if not _PREFIX.fullmatch(prefix):
        raise ValueError("id prefix must contain 2 to 16 lowercase alphanumeric characters")
    return f"{prefix}_{secrets.token_hex(16)}"
