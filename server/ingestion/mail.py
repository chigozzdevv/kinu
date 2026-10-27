import hashlib
import re
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from enum import StrEnum

from contracts import Confidence, Contract, Identifier, IngestionEvent, Severity, SourceResource
from pydantic import Field, field_validator


class MailTrust(StrEnum):
    MAILBOX = "mailbox"
    VERIFIED_RECEIPT = "verified-receipt"
    DEMO_FIXTURE = "demo-fixture"


class MailRule(Contract):
    id: Identifier
    organisation_id: Identifier
    sender: str = Field(min_length=3, max_length=254)
    provider: str = Field(min_length=1, max_length=64)
    connection_id: Identifier
    created_at: datetime
    revision: int = Field(default=0, ge=0)

    @field_validator("sender")
    @classmethod
    def exact_sender(cls, value: str) -> str:
        if not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value
        ):
            raise ValueError("an exact sender address is required")
        return value.casefold()


class CredentialHint(Contract):
    credential_id: Identifier
    provider_id: str = Field(min_length=1, max_length=256)


class EmailSignal(Contract):
    event: IngestionEvent
    signals: tuple[str, ...]
    transport: MailTrust
    matched_sender: bool


class EmailSource:
    def normalise(
        self,
        rule: MailRule,
        raw: bytes,
        received_at: datetime,
        hints: tuple[CredentialHint, ...],
        trust: MailTrust = MailTrust.MAILBOX,
    ) -> EmailSignal:
        if not raw or len(raw) > 262_144:
            raise ValueError("email must be at most 256 KiB")
        message = BytesParser(policy=policy.default).parsebytes(raw)
        addresses = getaddresses(message.get_all("From", []))
        if len(addresses) != 1 or addresses[0][1].casefold() != rule.sender:
            raise ValueError("email sender does not match the configured address")
        text = str(message.get("Subject", "")) + "\n"
        parts = list(message.walk())
        if len(parts) > 50:
            raise ValueError("email contains too many MIME parts")
        for part in parts:
            if (
                part.get_content_type() == "text/plain"
                and part.get_content_disposition() != "attachment"
            ):
                content = part.get_content()
                if isinstance(content, str):
                    text += content[:262_144] + "\n"
        matches = [
            hint
            for hint in hints[:500]
            if re.search(
                r"(?<![A-Za-z0-9_-])" + re.escape(hint.provider_id) + r"(?![A-Za-z0-9_-])", text
            )
        ]
        exposure = bool(
            re.search(r"\b(exposed|compromised|leaked|exposure)\b", text, re.IGNORECASE)
        )
        expiry = bool(re.search(r"\b(expiring|expires|expiration)\b", text, re.IGNORECASE))
        kind = (
            "credential-exposure-detected"
            if exposure
            else "credential-expiring"
            if expiry
            else "credential-security-review"
        )
        signals = tuple(
            name
            for name, present in (
                ("exposure-language", exposure),
                ("expiry-language", expiry),
                ("known-provider-id", len(matches) == 1),
            )
            if present
        )
        confidence = (
            Confidence.VERIFIED
            if trust is MailTrust.DEMO_FIXTURE
            else Confidence.HIGH
            if trust is MailTrust.VERIFIED_RECEIPT
            else Confidence.MEDIUM
        )
        observed = received_at
        if message.get("Date"):
            try:
                candidate = parsedate_to_datetime(str(message["Date"]))
                if candidate.tzinfo and received_at - timedelta(
                    days=7
                ) <= candidate <= received_at + timedelta(minutes=5):
                    observed = candidate
            except (TypeError, ValueError, OverflowError):
                pass
        identity = hashlib.sha256(rule.id.encode() + b"\0" + raw).hexdigest()
        event = IngestionEvent(
            id=f"mail_{identity[:40]}",
            organisation_id=rule.organisation_id,
            source="demo-email" if trust is MailTrust.DEMO_FIXTURE else "email",
            source_event_id=identity,
            kind=kind,
            observed_at=observed,
            severity=Severity.HIGH if exposure else Severity.MEDIUM,
            confidence=confidence,
            resource=SourceResource(
                connection_id=rule.connection_id,
                provider=rule.provider,
                provider_id=matches[0].provider_id if len(matches) == 1 else None,
            ),
            source_reference=f"kinu://mail/{rule.id}/{identity}",
            received_at=received_at,
        )
        return EmailSignal(event=event, signals=signals, transport=trust, matched_sender=True)
