import asyncio
import json
from datetime import datetime

from src.collectors.definitions.measurement import Measurement
from src.databases.text_file.textfiledb import TextFileDatabase


def make_measurement(
    metric="temperature",
    value=20.5,
    source="test",
    timestamp=None,
    unit="°C",
):
    return Measurement(
        timestamp=timestamp or datetime(2026, 8, 27, 12, 0),
        source=source,
        metric=metric,
        value=value,
        unit=unit,
    )


def test_store_creates_directory_and_daily_file(tmp_path):
    directory = tmp_path / "backup"
    database = TextFileDatabase(str(directory))

    measurement = make_measurement()

    asyncio.run(database.store([measurement]))

    assert directory.exists()
    assert directory.is_dir()

    file = directory / "2026-08-27.jsonl"

    assert file.exists()
    assert file.is_file()


def test_store_writes_measurement_as_json_line(tmp_path):
    database = TextFileDatabase(str(tmp_path))

    measurement = make_measurement(
        metric="temperature",
        value=20.5,
        source="sensor",
    )

    asyncio.run(database.store([measurement]))

    file = tmp_path / "2026-08-27.jsonl"
    lines = file.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1

    record = json.loads(lines[0])

    assert record == {
        "timestamp": "2026-08-27T12:00:00",
        "source": "sensor",
        "metric": "temperature",
        "value": 20.5,
        "unit": "°C",
    }


def test_store_writes_multiple_measurements(tmp_path):
    database = TextFileDatabase(str(tmp_path))

    measurements = [
        make_measurement(
            metric="temperature",
            value=20.5,
        ),
        make_measurement(
            metric="humidity",
            value=60.0,
            unit="%",
        ),
    ]

    asyncio.run(database.store(measurements))

    file = tmp_path / "2026-08-27.jsonl"
    lines = file.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2

    records = [json.loads(line) for line in lines]

    assert records[0]["metric"] == "temperature"
    assert records[0]["value"] == 20.5
    assert records[1]["metric"] == "humidity"
    assert records[1]["value"] == 60.0


def test_store_creates_separate_file_for_each_day(tmp_path):
    database = TextFileDatabase(str(tmp_path))

    measurements = [
        make_measurement(
            timestamp=datetime(2026, 8, 27, 23, 59),
        ),
        make_measurement(
            timestamp=datetime(2026, 8, 28, 0, 1),
        ),
    ]

    asyncio.run(database.store(measurements))

    file_1 = tmp_path / "2026-08-27.jsonl"
    file_2 = tmp_path / "2026-08-28.jsonl"

    assert file_1.exists()
    assert file_2.exists()

    assert len(file_1.read_text(encoding="utf-8").splitlines()) == 1
    assert len(file_2.read_text(encoding="utf-8").splitlines()) == 1


def test_store_appends_after_database_restart(tmp_path):
    measurement1 = make_measurement(value=20.5)

    database = TextFileDatabase(str(tmp_path))
    asyncio.run(database.store([measurement1]))
    database.close()

    # Simulate application restart.
    database = TextFileDatabase(str(tmp_path))

    measurement2 = make_measurement(value=21.0)
    asyncio.run(database.store([measurement2]))

    file = tmp_path / "2026-08-27.jsonl"
    lines = file.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["value"] == 20.5
    assert json.loads(lines[1])["value"] == 21.0


def test_store_preserves_unicode(tmp_path):
    database = TextFileDatabase(str(tmp_path))

    measurement = make_measurement(
        source="Wohnzimmer - Sensor äöü",
        metric="Temperatur °C",
        unit="°C",
    )

    asyncio.run(database.store([measurement]))

    file = tmp_path / "2026-08-27.jsonl"
    content = file.read_text(encoding="utf-8")

    assert "Wohnzimmer - Sensor äöü" in content
    assert "Temperatur °C" in content
    assert "°C" in content


def test_store_empty_measurements_creates_no_file(tmp_path):
    database = TextFileDatabase(str(tmp_path))

    asyncio.run(database.store([]))

    assert not list(tmp_path.iterdir())


def test_close_is_safe(tmp_path):
    database = TextFileDatabase(str(tmp_path))

    database.close()
