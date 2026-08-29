from datetime import datetime
from unittest.mock import Mock

import pytest

from src.collectors.definitions.measurement import Measurement
from src.collectors.iec.iec_collector import IecCollector

IEC_PAYLOAD = """
1-1:F.F(00000000)
1-1:0.0.0(001LGZ0058134272)
1-1:0.9.1(162655)
1-1:0.9.2(260815)
1-1:0.1.0(62)
1-1:0.1.2*62(2608010000)
1-1:0.1.2*61(2607010000)

1-1:1.5.0(00.000*kW)
1-1:1.6.0(00.641*kW)(2608091845)
1-1:1.6.0*62(02.142)(2607220915)

1-1:1.8.0(0018788.9*kWh)
1-1:1.8.0*62(0018750.0)

1-1:2.5.0(08.272*kW)
1-1:2.6.0(14.520*kW)(2608071515)
1-1:2.6.0*62(14.572)(2607081345)

1-1:2.8.0(0077347.5*kWh)
1-1:2.8.0*62(0076108.5)

1-1:5.8.0(0000139.8*kvarh)
1-1:5.8.0*62(0000139.7)

1-1:32.7.0(239.9*V)
1-1:52.7.0(240.8*V)
1-1:72.7.0(239.9*V)

1-1:31.7.0(010.69*A)
1-1:51.7.0(010.92*A)
1-1:71.7.0(010.52*A)

1-1:36.7.0(-002.54*kW)
1-1:56.7.0(-002.61*kW)
1-1:76.7.0(-002.49*kW)

1-1:16.7.0(-007.66*kW)

1-1:151.7.0(-000.25*kvar)
1-1:171.7.0(-000.23*kvar)
1-1:191.7.0(-000.33*kvar)

1-1:131.7.0(-000.82*kvar)

1-1:0.5.1.2(80.000*kW)
!
"""


@pytest.fixture
def collector():
    return IecCollector(
        port="/dev/ttyUSB0",
    )


def test_parse_returns_measurements(collector):
    result = collector._parse(IEC_PAYLOAD)

    assert isinstance(result, list)
    assert all(isinstance(measurement, Measurement) for measurement in result)


def test_parse_returns_only_current_metrics(collector):
    result = collector._parse(IEC_PAYLOAD)

    metrics = {measurement.metric for measurement in result}

    assert metrics == {
        "grid_import_power",
        "grid_export_power",
        "grid_import_energy_total",
        "grid_export_energy_total",
        "grid_active_power",
        "reactive_power_total",
    }


def test_parse_ignores_historical_values(collector):
    result = collector._parse(IEC_PAYLOAD)

    metrics = {measurement.metric for measurement in result}

    assert "grid_import_power_max" not in metrics
    assert "grid_export_power_max" not in metrics


def test_parse_ignores_unselected_current_values(collector):
    result = collector._parse(IEC_PAYLOAD)

    metrics = {measurement.metric for measurement in result}

    assert "voltage_l1" not in metrics
    assert "voltage_l2" not in metrics
    assert "voltage_l3" not in metrics

    assert "current_l1" not in metrics
    assert "current_l2" not in metrics
    assert "current_l3" not in metrics

    assert "active_power_l1" not in metrics
    assert "active_power_l2" not in metrics
    assert "active_power_l3" not in metrics

    assert "reactive_power_l1" not in metrics
    assert "reactive_power_l2" not in metrics
    assert "reactive_power_l3" not in metrics


def test_parse_current_power_import(collector):
    result = collector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(result, "grid_import_power")

    assert measurement.value == 0.0
    assert measurement.unit == "kW"


def test_parse_current_power_export(collector):
    result = collector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(result, "grid_export_power")

    assert measurement.value == 8.272
    assert measurement.unit == "kW"


def test_parse_total_import_energy(collector):
    result = collector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(result, "grid_import_energy_total")

    assert measurement.value == 18788.9
    assert measurement.unit == "kWh"
    assert measurement.source == "iec"


def test_parse_total_export_energy(collector):
    result = collector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(result, "grid_export_energy_total")

    assert measurement.value == 77347.5
    assert measurement.unit == "kWh"


def test_parse_total_active_power(collector):
    result = collector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(result, "grid_active_power")

    assert measurement.value == -7.66
    assert measurement.unit == "kW"


def test_parse_total_reactive_power(collector):
    result = collector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(result, "reactive_power_total")

    assert measurement.value == -0.82
    assert measurement.unit == "kvar"


def test_parse_measurement_source(collector):
    result = collector._parse(IEC_PAYLOAD)

    assert all(measurement.source == "iec" for measurement in result)


def test_parse_measurement_type(collector):
    result = collector._parse(IEC_PAYLOAD)

    assert all(measurement.measurement_type == "current" for measurement in result)


def test_parse_measurement_timestamp(collector):
    result = collector._parse(IEC_PAYLOAD)

    timestamps = {measurement.timestamp for measurement in result}

    assert len(timestamps) == 1

    timestamp = next(iter(timestamps))

    assert isinstance(timestamp, datetime)
    assert timestamp.tzinfo is not None


def test_parse_signed_values(collector):
    text = """
    1-1:16.7.0(-007.66*kW)
    1-1:131.7.0(-000.82*kvar)
    """

    result = collector._parse(text)

    assert (
        _find_measurement(
            result,
            "grid_active_power",
        ).value
        == -7.66
    )

    assert (
        _find_measurement(
            result,
            "reactive_power_total",
        ).value
        == -0.82
    )


def test_parse_plus_signed_values(collector):
    text = """
    1-1:131.7.0(+000.59*kvar)
    """

    result = collector._parse(text)

    measurement = _find_measurement(
        result,
        "reactive_power_total",
    )

    assert measurement.value == 0.59
    assert measurement.unit == "kvar"


def test_parse_accepts_obis_without_device_prefix(collector):
    text = """
    1.5.0(00.000*kW)
    2.5.0(08.272*kW)
    """

    result = collector._parse(text)

    assert len(result) == 2

    assert (
        _find_measurement(
            result,
            "grid_import_power",
        ).value
        == 0.0
    )

    assert (
        _find_measurement(
            result,
            "grid_export_power",
        ).value
        == 8.272
    )


def test_parse_empty_payload(collector):
    result = collector._parse("")

    assert result == []


def test_parse_unknown_obis(collector):
    text = """
    1-1:99.99.99(123.456*kW)
    """

    result = collector._parse(text)

    assert result == []


def test_parse_historical_value_is_not_returned_even_if_base_obis_is_current(collector):
    text = """
    1-1:1.8.0(0018788.9*kWh)
    1-1:1.8.0*62(0018750.0)
    """

    result = collector._parse(text)

    assert len(result) == 1

    measurement = result[0]

    assert measurement.metric == "grid_import_energy_total"
    assert measurement.value == 18788.9
    assert measurement.unit == "kWh"
    assert measurement.measurement_type == "current"


def test_collect_connects_when_not_connected(collector):
    collector.connected = False

    collector.protocol.connect = Mock()
    collector.protocol.read = Mock(
        return_value=IEC_PAYLOAD,
    )

    result = collector.collect()

    collector.protocol.connect.assert_called_once()
    collector.protocol.read.assert_called_once()

    assert len(result) == 6


def test_collect_does_not_reconnect_when_already_connected(collector):
    collector.connected = True

    collector.protocol.connect = Mock()
    collector.protocol.read = Mock(
        return_value=IEC_PAYLOAD,
    )

    result = collector.collect()

    collector.protocol.connect.assert_not_called()
    collector.protocol.read.assert_called_once()

    assert len(result) == 6


def test_connect(collector):
    collector.protocol.connect = Mock()

    collector.connect()

    collector.protocol.connect.assert_called_once()
    assert collector.connected is True


def test_disconnect(collector):
    collector.connected = True
    collector.protocol.disconnect = Mock()

    collector.disconnect()

    collector.protocol.disconnect.assert_called_once()
    assert collector.connected is False


def test_parse_value_without_unit(collector):
    value, unit = collector._parse_value("123.45")

    assert value == 123.45
    assert unit == ""


def test_parse_value_with_unit(collector):
    value, unit = collector._parse_value("123.45*kW")

    assert value == 123.45
    assert unit == "kW"


def test_parse_ignores_current_obis_without_definition(collector, monkeypatch):
    text = """
    1-1:1.5.0(00.000*kW)
    """

    monkeypatch.setattr(
        "src.collectors.iec.iec_collector.get_obis_definition",
        lambda obis: None,
    )

    result = collector._parse(text)

    assert result == []


def _find_measurement(
    measurements: list[Measurement],
    metric: str,
) -> Measurement:
    for measurement in measurements:
        if measurement.metric == metric:
            return measurement

    raise AssertionError(f"No measurement found for metric {metric}")


def test_parse_includes_total_energy_values(collector):
    text = """
    1-1:1.8.0(00123.456*kWh)
    1-1:2.8.0(00045.678*kWh)
    1-1:1.5.0(01.234*kW)
    """

    measurements = collector._parse(text)

    assert [measurement.metric for measurement in measurements] == [
        "grid_import_energy_total",
        "grid_export_energy_total",
        "grid_import_power",
    ]

    assert measurements[0].value == 123.456
    assert measurements[0].unit == "kWh"

    assert measurements[1].value == 45.678
    assert measurements[1].unit == "kWh"


def test_parse_ignores_unsupported_values(collector):
    text = """
    1-1:1.8.0(00123.456*kWh)
    1-1:2.8.0(00045.678*kWh)
    1-1:5.8.0(00010.000*kvarh)
    1-1:32.7.0(230.0*V)
    """

    measurements = collector._parse(text)

    assert {measurement.metric for measurement in measurements} == {
        "grid_import_energy_total",
        "grid_export_energy_total",
    }


def test_parse_uses_configured_source():
    collector = IecCollector(
        port="/dev/ttyUSB0",
        source="grid_iec",
    )

    result = collector._parse("""
        1-1:1.8.0(0018788.9*kWh)
        1-1:2.8.0(0077347.5*kWh)
        """)

    assert all(measurement.source == "grid_iec" for measurement in result)


def test_parse_uses_household_source():
    collector = IecCollector(
        port="/dev/ttyUSB1",
        source="household_iec",
    )

    result = collector._parse("""
        1-1:1.8.0(0018788.9*kWh)
        1-1:2.8.0(0077347.5*kWh)
        """)

    assert all(measurement.source == "household_iec" for measurement in result)
