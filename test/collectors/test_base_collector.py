import pytest

from src.collectors.base_collector import BaseCollector


class TestCollector(BaseCollector):
    def collect(self):
        return super().collect()


def test_base_collector_collect_raises_not_implemented():
    collector = TestCollector()

    with pytest.raises(NotImplementedError):
        collector.collect()


def test_base_collector_connect_does_nothing():
    collector = TestCollector()

    assert collector.connect() is None


def test_base_collector_disconnect_does_nothing():
    collector = TestCollector()

    assert collector.disconnect() is None
