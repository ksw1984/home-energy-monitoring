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


def test_base_collector_storage_interval_defaults_to_none():
    collector = MockCollector()

    assert collector.storage_interval is None


def test_base_collector_configure_runtime_updates_storage_interval():
    collector = MockCollector()

    collector.configure_runtime(
        interval=1,
        enabled=False,
        storage_interval=5,
    )

    assert collector.interval == 1
    assert collector.enabled is False
    assert collector.storage_interval == 5


def test_base_collector_configure_runtime_clears_storage_interval():
    collector = MockCollector(storage_interval=5)

    collector.configure_runtime(
        interval=1,
        enabled=True,
        storage_interval=None,
    )

    assert collector.storage_interval is None


def test_base_collector_keeps_storage_interval_when_new_value_is_invalid(caplog):
    collector = MockCollector(storage_interval=5)

    with caplog.at_level("WARNING"):
        collector.configure_runtime(
            interval=1,
            enabled=True,
            storage_interval=0,
        )

    assert collector.storage_interval == 5
    assert "Invalid storage_interval=0" in caplog.text
