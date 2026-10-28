from core.config import Settings
from core.storage.database import Database


def application_database(config: Settings) -> Database:
    return Database(config.mongo_url.get_secret_value(), config.mongo_database)
