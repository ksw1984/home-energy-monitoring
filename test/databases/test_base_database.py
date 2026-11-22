from src.databases.base_database import BaseDatabase

import pytest


def test_base_database_requires_store_implementation():
    with pytest.raises(TypeError, match="abstract"):
        BaseDatabase(type="test")
