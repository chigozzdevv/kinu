import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Any

import dkim
from dkim.util import parse_tag_value


async def authenticated(raw: bytes, sender: str, dns: Callable[..., Any] | None = None) -> bool:
    try:
        async with asyncio.timeout(8):
            return await asyncio.to_thread(_verify, raw, sender, dns)
    except Exception:
        return False


def _verify(raw: bytes, sender: str, dns: Callable[..., Any] | None) -> bool:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    required = {"from", "subject", "date"}
    if any(len(message.get_all(name, [])) != 1 for name in required):
        return False
    for name in ("content-type", "content-transfer-encoding", "mime-version"):
        if len(message.get_all(name, [])) > 1:
            return False
        if message.get(name):
            required.add(name)
    date = parsedate_to_datetime(str(message["Date"]))
    now = datetime.now(UTC)
    if date.tzinfo is None or not now - timedelta(days=7) <= date <= now + timedelta(minutes=5):
        return False
    domain = sender.rsplit("@", 1)[1].casefold().encode()
    verifier = dkim.DKIM(raw, minkey=1024, timeout=3)
    for index, signature in enumerate(message.get_all("DKIM-Signature", [])[:5]):
        tags = parse_tag_value(str(signature).encode())
        signed = {item.strip().lower().decode() for item in tags.get(b"h", b"").split(b":")}
        if (
            tags.get(b"d", b"").lower() != domain
            or b"l" in tags
            or tags.get(b"a") not in {b"rsa-sha256", b"ed25519-sha256"}
            or not required <= signed
        ):
            continue
        if verifier.verify(idx=index, **({"dnsfunc": dns} if dns else {})):
            return True
    return False
