import re
from urllib.parse import urlsplit


def normalize(value: str, prefix: str | None, origin: str) -> str:
    value = value.strip()
    if prefix:
        linked, source = urlsplit(value), urlsplit(origin)
        if (
            linked.scheme not in {"", "https"}
            or (linked.netloc and linked.netloc != source.netloc)
            or linked.query
            or linked.fragment
            or not linked.path.startswith(prefix)
        ):
            raise ValueError("Credential link escapes its provider path")
        value = linked.path.removeprefix(prefix)
    if not re.fullmatch(r"[A-Za-z0-9_:-][A-Za-z0-9_.:-]{0,255}", value):
        raise ValueError("Credential identifier is invalid")
    return value
