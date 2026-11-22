import asyncio

from src.databases.base_database import BaseDatabase
from src.shared.measurement import Measurement

import pytest


class MockDatabase(BaseDatabase):
    async def store(self, measurements: list[Measurement]) -> None:
        await super().store(measurements)

    def close(self) -> None:
        super().close()


def test_base_database_store_raises_not_implemented():
    database = MockDatabase(type="test")

    with pytest.raises(NotImplementedError):
        asyncio.run(database.store([]))
