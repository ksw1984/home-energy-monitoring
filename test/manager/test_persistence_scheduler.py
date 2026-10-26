from src.manager.persistence_scheduler import PersistenceScheduler

import pytest


def test_scheduler_persists_every_successful_collection_without_storage_interval():
    scheduler = PersistenceScheduler(interval=1.0, storage_interval=None)

    assert [scheduler.record_success() for _ in range(3)] == [True, True, True]
    assert scheduler.cycles == 1


def test_scheduler_persists_first_then_every_nth_successful_collection():
    scheduler = PersistenceScheduler(interval=1.0, storage_interval=5)

    assert [scheduler.record_success() for _ in range(11)] == [
        True,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        True,
    ]
    assert scheduler.cycles == 5
    assert scheduler.effective_storage_interval_s == 5.0


def test_scheduler_rounds_fractional_ratio_to_nearest_cycle_count():
    scheduler = PersistenceScheduler(interval=0.5, storage_interval=5)

    assert scheduler.cycles == 10
    assert scheduler.effective_storage_interval_s == 5.0


def test_scheduler_clamps_storage_interval_shorter_than_interval(caplog):
    with caplog.at_level("WARNING"):
        scheduler = PersistenceScheduler(interval=10.0, storage_interval=5)

    assert scheduler.cycles == 1
    assert scheduler.record_success() is True
    assert "storage_interval=5s at interval=10.0s resolves to n=1" in caplog.text


def test_scheduler_warns_when_rounded_cadence_deviates_more_than_five_percent(caplog):
    with caplog.at_level("WARNING"):
        scheduler = PersistenceScheduler(interval=1.1, storage_interval=5)

    # 5 / 1.1 = 4.545 -> n=5 -> 5.5 s, a 10% deviation.
    assert scheduler.cycles == 5
    assert scheduler.effective_storage_interval_s == pytest.approx(5.5)
    assert "10.0% from requested" in caplog.text


def test_scheduler_resets_after_cadence_change():
    scheduler = PersistenceScheduler(interval=1.0, storage_interval=5)

    assert scheduler.record_success() is True
    assert scheduler.record_success() is False

    assert scheduler.update(interval=1.0, storage_interval=10) is True
    assert scheduler.record_success() is True
    assert scheduler.record_success() is False


def test_scheduler_update_is_noop_when_cadence_is_unchanged():
    scheduler = PersistenceScheduler(interval=1.0, storage_interval=5)

    assert scheduler.record_success() is True
    assert scheduler.update(interval=1.0, storage_interval=5) is False
    assert scheduler.record_success() is False


def test_scheduler_warns_when_collection_overruns_interval(caplog):
    scheduler = PersistenceScheduler(interval=1.0, storage_interval=5)

    with caplog.at_level("WARNING"):
        assert scheduler.record_success(elapsed_s=1.25) is True

    assert "longer than interval=1.000s" in caplog.text
    assert "database spacing may exceed the configured 5.000s" in caplog.text


def test_scheduler_rejects_non_positive_interval():
    with pytest.raises(ValueError, match="interval must be positive"):
        PersistenceScheduler(interval=0, storage_interval=5)


@pytest.mark.parametrize("storage_interval", [0, -1, 5.5, True])
def test_scheduler_rejects_invalid_storage_interval(storage_interval):
    with pytest.raises(ValueError, match="storage_interval must be a positive integer or None"):
        PersistenceScheduler(interval=1.0, storage_interval=storage_interval)
