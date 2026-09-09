from src.collectors.base_collector import BaseCollector

import pytest


class MockCollector(BaseCollector):
    def __init__(self, **kwargs):
        super().__init__(source="mock", **kwargs)

    def collect(self):
        return super().collect()


def test_base_collector_collect_raises_not_implemented():
    collector = MockCollector()

    with pytest.raises(NotImplementedError):
        collector.collect()


def test_base_collector_connect_does_nothing():
    collector = MockCollector()

    assert collector.connect() is None


def test_base_collector_disconnect_does_nothing():
    collector = MockCollector()

    assert collector.disconnect() is None
