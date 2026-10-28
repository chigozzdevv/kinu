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
        "host_bindings": [
            IndexModel([("organisation_id", 1), ("binding_id", 1)], unique=True),
            IndexModel(
                [("organisation_id", 1), ("connection_id", 1), ("reference", 1)], unique=True
            ),
        ],
        "browser_locks": [IndexModel([("organisation_id", 1), ("connection_id", 1)], unique=True)],
        "browser_sessions": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "browser_actions": [
            IndexModel([("organisation_id", 1), ("session_id", 1), ("id", 1)], unique=True)
        ],
        "browser_captures": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "browser_activity": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "runtime_locks": [
            IndexModel([("organisation_id", 1), ("connection_id", 1), ("resource", 1)], unique=True)
        ],
        "host_rollouts": [
            IndexModel([("organisation_id", 1), ("run_id", 1), ("binding_id", 1)], unique=True)
        ],
        "runs": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "run_locks": [IndexModel([("organisation_id", 1), ("credential_id", 1)], unique=True)],
        "commands": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "triggers": [
            IndexModel([("organisation_id", 1), ("source", 1), ("event_id", 1)], unique=True)
        ],
        "outbox": [IndexModel([("organisation_id", 1), ("id", 1)], unique=True)],
        "notification_deliveries": [
            IndexModel([("organisation_id", 1), ("id", 1)], unique=True),
            IndexModel([("state", 1), ("available_at", 1), ("lease_expires_at", 1)]),
            IndexModel([("state", 1), ("status_due", 1)]),
        ],
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
