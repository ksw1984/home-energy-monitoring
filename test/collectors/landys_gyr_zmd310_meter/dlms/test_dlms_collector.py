from datetime import datetime
from unittest.mock import Mock

from src.collectors.definitions.common.measurement import Measurement
from src.collectors.landys_gyr_zmd310_meter.dlms.dlms_collector import DlmsCollector

import pytest
import serial


@pytest.fixture
def collector():
    return DlmsCollector(
        port="/dev/ttyUSB0",
        source="grid_dlms",
    )


def test_connect_opens_protocol_connection(collector):
    collector.protocol.connect = Mock()

    collector.connect()

    collector.protocol.connect.assert_called_once()
    assert collector.connected is True


def test_connect_does_nothing_when_already_connected(collector):
    collector.connected = True
    collector.protocol.connect = Mock()

    collector.connect()

    collector.protocol.connect.assert_not_called()
    assert collector.connected is True


def test_connect_does_nothing_when_disabled(collector):
    collector.enabled = False
    collector.protocol.connect = Mock()

    collector.connect()

    collector.protocol.connect.assert_not_called()
    assert collector.connected is False


def test_connect_marks_collector_disconnected_on_failure(collector):
    collector.protocol.connect = Mock(
        side_effect=serial.SerialException("connection failed"),
    )

    with pytest.raises(serial.SerialException):
        collector.connect()

    assert collector.connected is False


def test_disconnect_closes_protocol_connection(collector):
    collector.connected = True
    collector.protocol.disconnect = Mock()

    collector.disconnect()

    collector.protocol.disconnect.assert_called_once()
    assert collector.connected is False


def test_collect_connects_when_not_connected(collector):
    collector.connected = False

    collector.protocol.connect = Mock()
    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
            "2.8.0": 67.89,
        },
    )

    result = collector.collect()

    collector.protocol.connect.assert_called_once()
    collector.protocol.read.assert_called_once()

    assert collector.connected is True
    assert len(result) == 2


def test_collect_reuses_existing_connection(collector):
    collector.connected = True

    collector.protocol.connect = Mock()
    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
        },
    )

    result = collector.collect()

    collector.protocol.connect.assert_not_called()
    collector.protocol.read.assert_called_once()

    assert len(result) == 1
    assert collector.connected is True


def test_collect_creates_measurements(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
            "2.8.0": 67.89,
        },
    )

    result = collector.collect()

    assert all(isinstance(measurement, Measurement) for measurement in result)

    assert {measurement.metric for measurement in result} == {
        "grid_import_energy_total",
        "grid_export_energy_total",
    }


def test_collect_maps_values_and_units(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
            "2.8.0": 67.89,
        },
    )

    result = collector.collect()

    import_energy = _find_measurement(
        result,
        "grid_import_energy_total",
    )
    export_energy = _find_measurement(
        result,
        "grid_export_energy_total",
    )

    assert import_energy.value == 123.45
    assert import_energy.unit == "kWh"

    assert export_energy.value == 67.89
    assert export_energy.unit == "kWh"


def test_collect_uses_configured_source(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
        },
    )

    result = collector.collect()

    assert all(measurement.source == "grid_dlms" for measurement in result)


def test_collect_sets_timestamp(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
        },
    )

    result = collector.collect()

    assert len(result) == 1
    assert isinstance(result[0].timestamp, datetime)
    assert result[0].timestamp.tzinfo is not None


def test_collect_returns_empty_when_disabled(collector):
    collector.enabled = False
    collector.protocol.read = Mock()

    result = collector.collect()

    assert result == []
    collector.protocol.read.assert_not_called()


def test_collect_handles_missing_obis_definition(collector, monkeypatch):
    collector.connected = True

    collector.protocol.read = Mock(
        return_value={
            "1.8.0": 123.45,
        },
    )

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_collector.get_obis_definition",
        lambda obis: None,
    )

    result = collector.collect()

    assert result == []


def test_collect_rejects_non_numeric_value(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        return_value={
            "1.8.0": "123.45",
        },
    )

    with pytest.raises(TypeError, match="Expected numeric value"):
        collector.collect()


def test_collect_disconnects_after_serial_error(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        side_effect=serial.SerialException("read failed"),
    )
    collector.protocol.disconnect = Mock()

    with pytest.raises(serial.SerialException):
        collector.collect()

    assert collector.connected is False
    collector.protocol.disconnect.assert_called_once()


def test_collect_disconnects_after_timeout(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        side_effect=TimeoutError("timeout"),
    )
    collector.protocol.disconnect = Mock()

    with pytest.raises(TimeoutError):
        collector.collect()

    assert collector.connected is False
    collector.protocol.disconnect.assert_called_once()


def test_collect_disconnects_after_runtime_error(collector):
    collector.connected = True

    collector.protocol.read = Mock(
        side_effect=RuntimeError("DLMS error"),
    )
    collector.protocol.disconnect = Mock()

    with pytest.raises(RuntimeError):
        collector.collect()

    assert collector.connected is False
    collector.protocol.disconnect.assert_called_once()


def test_collect_passes_current_obis_to_protocol(collector, monkeypatch):
    collector.connected = True

    current_obis = {
        "1.5.0",
        "2.5.0",
        "1.8.0",
        "2.8.0",
        "16.7.0",
        "131.7.0",
    }

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_collector.CURRENT_OBIS",
        current_obis,
    )

    collector.protocol.read = Mock(return_value={})

    collector.collect()

    collector.protocol.read.assert_called_once_with(current_obis)


def _find_measurement(
    measurements: list[Measurement],
    metric: str,
) -> Measurement:
    for measurement in measurements:
        if measurement.metric == metric:
            return measurement

    raise AssertionError(f"No measurement found for metric {metric}")
