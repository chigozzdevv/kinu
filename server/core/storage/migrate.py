import asyncio

from core.config import Settings
from core.storage.factory import application_database


async def migrate() -> None:
    config = Settings()
    database = application_database(config)
    try:
        await database.open()
        await database.migrate()
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(migrate())
