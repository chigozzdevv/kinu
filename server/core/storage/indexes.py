from pymongo import IndexModel

from core.storage.database import Database


async def ensure_indexes(database: Database) -> None:
    indexes = {
        "organisations": [IndexModel("id", unique=True)],
        "members": [
            IndexModel([("organisation_id", 1), ("subject", 1)], unique=True),
            IndexModel([("organisation_id", 1), ("id", 1)], unique=True),
            IndexModel("subject"),
        ],
        "invitations": [
            IndexModel([("organisation_id", 1), ("id", 1)], unique=True),
            IndexModel("email"),
        ],
        "inventory": [
            IndexModel([("organisation_id", 1), ("kind", 1), ("id", 1)], unique=True),
            IndexModel(
                [("organisation_id", 1), ("data.credential_id", 1), ("data.number", 1)],
                unique=True,
                partialFilterExpression={"kind": "controls"},
                name="control_number",
            ),
            IndexModel(
                [("organisation_id", 1), ("data.source", 1), ("data.source_event_id", 1)],
                unique=True,
                partialFilterExpression={"kind": "ingestion-event"},
                name="source_event",
            ),
            IndexModel(
                [("organisation_id", 1), ("data.event_id", 1)],
                unique=True,
                partialFilterExpression={"kind": "incident"},
                name="incident_event",
            ),
        ],
        "runs": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "run_locks": [IndexModel([("organisation_id", 1), ("credential_id", 1)], unique=True)],
        "commands": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "triggers": [
            IndexModel([("organisation_id", 1), ("source", 1), ("event_id", 1)], unique=True)
        ],
        "outbox": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "audit": [
            IndexModel([("organisation_id", 1), ("id", 1)], unique=True),
            IndexModel([("organisation_id", 1), ("sequence", 1)], unique=True),
        ],
        "executions": [
            IndexModel([("organisation_id", 1), ("run_id", 1), ("stage", 1)], unique=True)
        ],
        "mail": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "users": [IndexModel("email", unique=True), IndexModel("id", unique=True)],
        "sessions": [
            IndexModel("token_hash", unique=True),
            IndexModel("expires_on", expireAfterSeconds=0),
        ],
        "auth_forms": [
            IndexModel("token_hash", unique=True),
            IndexModel("expires_on", expireAfterSeconds=0),
        ],
        "auth_limits": [
            IndexModel("key", unique=True),
            IndexModel("expires_on", expireAfterSeconds=0),
        ],
        "auth_codes": [
            IndexModel([("email", 1), ("purpose", 1)], unique=True),
            IndexModel("expires_on", expireAfterSeconds=0),
        ],
    }
    for name, definitions in indexes.items():
        await database.collection(name).create_indexes(definitions)
