import asyncio

import pytest

from src.collectors.definitions.measurement import Measurement
from src.databases.base_database import BaseDatabase


class TestDatabase(BaseDatabase):
    async def store(self, measurements: list[Measurement]) -> None:
        await super().store(measurements)

    def close(self) -> None:
        super().close()


def test_base_database_store_raises_not_implemented():
    database = TestDatabase()

    with pytest.raises(NotImplementedError):
        asyncio.run(database.store([]))


def test_base_database_close_raises_not_implemented():
    database = TestDatabase()

    with pytest.raises(NotImplementedError):
        database.close()
