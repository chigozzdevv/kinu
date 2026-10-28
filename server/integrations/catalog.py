from dataclasses import dataclass

from core.errors import ResourceNotFoundError


@dataclass(frozen=True, slots=True)
class Integration:
    id: str
    name: str
    category: str
    authorization: str
    fields: tuple[str, ...] = ()


CATALOG = (
    Integration("gmail", "Gmail", "incident", "oauth", ("client_id", "client_secret")),
    Integration("vercel", "Vercel", "runtime", "oauth", ("slug", "client_id", "client_secret")),
    Integration("railway", "Railway", "runtime", "oauth", ("client_id", "client_secret")),
    Integration("render", "Render", "runtime", "api-key"),
    Integration("doppler", "Doppler", "secret-store", "api-key"),
)


def integration(identity: str) -> Integration:
    for item in CATALOG:
        if item.id == identity:
            return item
    raise ResourceNotFoundError("Unknown integration")
