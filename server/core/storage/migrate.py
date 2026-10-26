import asyncio

from core.config import Settings
from core.storage.database import Database


async def migrate() -> None:
    config = Settings()
    if config.database_url is None:
        raise ValueError("KINU_DATABASE_URL is required for database migrations")
    database = Database(config.database_url.get_secret_value())
    try:
        await database.open()
        await database.migrate()
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(migrate())
