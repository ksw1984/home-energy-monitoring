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


def test_parse_returns_measurements():
    result = IecCollector._parse(IEC_PAYLOAD)

    assert isinstance(result, list)
    assert all(isinstance(measurement, Measurement) for measurement in result)


def test_parse_returns_only_current_obis():
    result = IecCollector._parse(IEC_PAYLOAD)

    obis_codes = {measurement.obis for measurement in result}

    assert obis_codes == {
        "1.5.0",
        "2.5.0",
        "1.8.0",
        "2.8.0",
        "16.7.0",
        "131.7.0",
    }


def test_parse_ignores_historical_values():
    result = IecCollector._parse(IEC_PAYLOAD)

    obis_codes = [measurement.obis for measurement in result]

    assert "1.6.0" not in obis_codes
    assert "1.8.0*62" not in obis_codes
    assert "2.6.0" not in obis_codes
    assert "2.6.0*62" not in obis_codes
    assert "2.8.0*62" not in obis_codes


def test_parse_ignores_unselected_current_values():
    result = IecCollector._parse(IEC_PAYLOAD)

    obis_codes = {measurement.obis for measurement in result}

    assert "32.7.0" not in obis_codes
    assert "52.7.0" not in obis_codes
    assert "72.7.0" not in obis_codes

    assert "31.7.0" not in obis_codes
    assert "51.7.0" not in obis_codes
    assert "71.7.0" not in obis_codes

    assert "36.7.0" not in obis_codes
    assert "56.7.0" not in obis_codes
    assert "76.7.0" not in obis_codes

    assert "151.7.0" not in obis_codes
    assert "171.7.0" not in obis_codes
    assert "191.7.0" not in obis_codes


def test_parse_current_power_import():
    result = IecCollector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(
        result,
        "1.5.0",
    )

    assert measurement.value == 0.0
    assert measurement.unit == "kW"
    assert measurement.obis == "1.5.0"


def test_parse_current_power_export():
    result = IecCollector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(
        result,
        "2.5.0",
    )

    assert measurement.value == 8.272
    assert measurement.unit == "kW"
    assert measurement.obis == "2.5.0"


def test_parse_total_import_energy():
    result = IecCollector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(
        result,
        "1.8.0",
    )

    assert measurement.value == 18788.9
    assert measurement.unit == "kWh"
    assert measurement.obis == "1.8.0"


def test_parse_total_export_energy():
    result = IecCollector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(
        result,
        "2.8.0",
    )

    assert measurement.value == 77347.5
    assert measurement.unit == "kWh"
    assert measurement.obis == "2.8.0"


def test_parse_total_active_power():
    result = IecCollector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(
        result,
        "16.7.0",
    )

    assert measurement.value == -7.66
    assert measurement.unit == "kW"
    assert measurement.obis == "16.7.0"


def test_parse_total_reactive_power():
    result = IecCollector._parse(IEC_PAYLOAD)

    measurement = _find_measurement(
        result,
        "131.7.0",
    )

    assert measurement.value == -0.82
    assert measurement.unit == "kvar"
    assert measurement.obis == "131.7.0"


def test_parse_measurement_source():
    result = IecCollector._parse(IEC_PAYLOAD)

    assert all(measurement.source == "iec" for measurement in result)


def test_parse_measurement_timestamp():
    result = IecCollector._parse(IEC_PAYLOAD)

    timestamps = {measurement.timestamp for measurement in result}

    assert len(timestamps) == 1

    timestamp = next(iter(timestamps))

    assert isinstance(timestamp, datetime)
    assert timestamp.tzinfo is not None


def test_parse_signed_values():
    text = """
    1-1:16.7.0(-007.66*kW)
    1-1:131.7.0(-000.82*kvar)
    """

    result = IecCollector._parse(text)

    assert (
        _find_measurement(
            result,
            "16.7.0",
        ).value
        == -7.66
    )

    assert (
        _find_measurement(
            result,
            "131.7.0",
        ).value
        == -0.82
    )


def test_parse_plus_signed_values():
    text = """
    1-1:131.7.0(+000.59*kvar)
    """

    result = IecCollector._parse(text)

    measurement = _find_measurement(
        result,
        "131.7.0",
    )

    assert measurement.value == 0.59


def test_parse_accepts_obis_without_device_prefix():
    text = """
    1.5.0(00.000*kW)
    2.5.0(08.272*kW)
    """

    result = IecCollector._parse(text)

    assert len(result) == 2

    assert (
        _find_measurement(
            result,
            "1.5.0",
        ).value
        == 0.0
    )

    assert (
        _find_measurement(
            result,
            "2.5.0",
        ).value
        == 8.272
    )


def test_parse_empty_payload():
    result = IecCollector._parse("")

    assert result == []


def test_parse_unknown_obis():
    text = """
    1-1:99.99.99(123.456*kW)
    """

    result = IecCollector._parse(text)

    assert result == []


def test_parse_historical_value_is_not_returned_even_if_base_obis_is_current():
    text = """
    1-1:1.8.0(0018788.9*kWh)
    1-1:1.8.0*62(0018750.0)
    """

    result = IecCollector._parse(text)

    assert len(result) == 1

    measurement = result[0]

    assert measurement.obis == "1.8.0"
    assert measurement.value == 18788.9


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


def test_disconnect(collector):
    collector.connected = True
    collector.protocol.disconnect = Mock()

    collector.disconnect()

    collector.protocol.disconnect.assert_called_once()
    assert collector.connected is False


def _find_measurement(
    measurements: list[Measurement],
    obis: str,
) -> Measurement:
    for measurement in measurements:
        if measurement.obis == obis:
            return measurement

    raise AssertionError(f"No measurement found for OBIS {obis}")
