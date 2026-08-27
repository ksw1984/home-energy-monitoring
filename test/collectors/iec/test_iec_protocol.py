from unittest.mock import Mock, patch

import pytest
import serial

from src.collectors.iec.iec_protocol import IecProtocol


def test_open_serial_uses_iec_serial_settings():
    protocol = IecProtocol(
        port="/dev/ttyUSB0",
        timeout=0.5,
    )

    with patch(
        "src.collectors.iec.iec_protocol.serial.Serial",
    ) as serial_cls:
        serial_connection = protocol._open_serial(9600)

    serial_cls.assert_called_once_with(
        port="/dev/ttyUSB0",
        baudrate=9600,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.5,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )

    assert serial_connection is serial_cls.return_value


def test_get_baud_rate_9600():
    identification = b"/LGZ5\\2ZMD3104107.B40\r\n"

    baud_code, baud_rate = IecProtocol._get_baud_rate(identification)

    assert baud_code == 5
    assert baud_rate == 9600


def test_get_baud_rate_300():
    identification = b"/LGZ0\\2ZMD3104107.B40\r\n"

    baud_code, baud_rate = IecProtocol._get_baud_rate(identification)

    assert baud_code == 0
    assert baud_rate == 300


def test_get_baud_rate_19200():
    identification = b"/LGZ6\\2ZMD3104107.B40\r\n"

    baud_code, baud_rate = IecProtocol._get_baud_rate(identification)

    assert baud_code == 6
    assert baud_rate == 19200


def test_get_baud_rate_invalid_identification():
    with pytest.raises(
        RuntimeError,
        match="Could not determine IEC baud rate",
    ):
        IecProtocol._get_baud_rate(b"invalid response\r\n")


def test_get_baud_rate_unsupported_code():
    identification = b"/LGZ9\\2ZMD3104107.B40\r\n"

    with pytest.raises(
        RuntimeError,
        match="Unsupported IEC baud rate code: 9",
    ):
        IecProtocol._get_baud_rate(identification)


def test_read_identification_times_out_without_terminator():
    serial_connection = Mock()
    serial_connection.read.return_value = b"/LGZ5"

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[
            0.0,  # start
            0.0,  # first loop condition
            6.0,  # second loop condition -> timeout
        ],
    ):
        result = IecProtocol._read_identification(
            serial_connection,
            timeout=5.0,
        )

    assert result == b"/LGZ5"
    serial_connection.read.assert_called_once_with(64)


def test_read_identification_ignores_empty_reads():
    serial_connection = Mock()

    serial_connection.read.side_effect = [
        b"",
        b"/LGZ5\\2ZMD3104107.B40\r\n",
    ]

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[
            0.0,  # start
            0.0,  # first loop condition
            0.0,  # second loop condition
        ],
    ):
        result = IecProtocol._read_identification(
            serial_connection,
            timeout=5.0,
        )

    assert result == b"/LGZ5\\2ZMD3104107.B40\r\n"
    assert serial_connection.read.call_count == 2
    serial_connection.read.assert_called_with(64)


def test_connect_closes_initial_serial_when_handshake_fails():
    protocol = IecProtocol()

    initial_serial = Mock()

    with (
        patch.object(
            protocol,
            "_open_serial",
            return_value=initial_serial,
        ),
        patch.object(
            protocol,
            "_read_identification",
            side_effect=RuntimeError("invalid identification"),
        ),
        pytest.raises(
            RuntimeError,
            match="invalid identification",
        ),
    ):
        protocol.connect()

    initial_serial.close.assert_called_once()
    assert protocol.serial is None


def test_read_stops_after_one_second_without_data():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.read.return_value = b""

    protocol.serial = serial_connection

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[
            0.0,  # start
            1.1,  # now
        ],
    ):
        result = protocol.read()

    assert result == ""
    serial_connection.read.assert_called_once_with(256)


def test_read_stops_after_thirty_second_overall_timeout():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.read.side_effect = [
        b"some data",
        b"more data",
    ]

    protocol.serial = serial_connection

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[
            0.0,  # start
            0.1,  # last_rx after first chunk
            0.1,  # now after first chunk
            29.9,  # last_rx after second chunk
            30.1,  # now after second chunk
        ],
    ):
        result = protocol.read()

    assert result == "some datamore data"
    assert serial_connection.read.call_count == 2


def test_read_reads_remaining_data_after_etx():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.read.side_effect = [
        b"\x02payload\x03",
        b"\x00trailing",
    ]

    protocol.serial = serial_connection

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        return_value=0.0,
    ):
        result = protocol.read()

    assert result == "payload"
    serial_connection.read.assert_any_call(256)
    serial_connection.read.assert_any_call(64)


def test_extract_payload():
    data = b"\x02" b"1-1:1.5.0(00.000*kW)\r\n" b"1-1:2.5.0(08.272*kW)\r\n" b"\x03" b"\x00"

    result = IecProtocol._extract_payload(data)

    assert result == ("1-1:1.5.0(00.000*kW)\r\n" "1-1:2.5.0(08.272*kW)\r\n")


def test_extract_payload_without_stx():
    data = b"1-1:1.5.0(00.000*kW)\r\n"

    result = IecProtocol._extract_payload(data)

    assert result == "1-1:1.5.0(00.000*kW)\r\n"


def test_extract_payload_without_etx():
    data = b"\x02" b"1-1:1.5.0(00.000*kW)\r\n"

    result = IecProtocol._extract_payload(data)

    assert result == "1-1:1.5.0(00.000*kW)\r\n"


def test_extract_empty_payload():
    assert IecProtocol._extract_payload(b"") == ""


def test_disconnect_when_not_connected():
    protocol = IecProtocol()

    protocol.disconnect()

    assert protocol.serial is None


def test_read_requires_connection():
    protocol = IecProtocol()

    with pytest.raises(
        RuntimeError,
        match="IEC collectors is not connected",
    ):
        protocol.read()


def test_connect_negotiates_9600_baud():
    protocol = IecProtocol(
        port="/dev/ttyUSB0",
    )

    identification = b"/LGZ5\\2ZMD3104107.B40\r\n"

    initial_serial = Mock()
    data_serial = Mock()

    initial_serial.read.side_effect = [
        identification,
    ]

    with patch.object(
        protocol,
        "_open_serial",
        side_effect=[
            initial_serial,
            data_serial,
        ],
    ) as open_serial:

        protocol.connect()

    assert protocol.data_baud == 9600
    assert protocol.serial is data_serial

    assert open_serial.call_count == 2

    open_serial.assert_any_call(300)
    open_serial.assert_any_call(9600)

    initial_serial.write.assert_any_call(b"/?!\r\n")

    initial_serial.write.assert_any_call(b"\x06050\r\n")

    initial_serial.close.assert_called_once()


def test_multiple_reads_use_same_open_connection():
    protocol = IecProtocol()

    serial_connection = Mock()

    serial_connection.read.side_effect = [
        # First IEC response
        b"\x02" b"1-1:1.5.0(00.000*kW)" b"\x03",
        b"",
        # Second IEC response
        b"\x02" b"1-1:1.5.0(00.123*kW)" b"\x03",
        b"",
    ]

    protocol.serial = serial_connection

    first = protocol.read()
    second = protocol.read()

    assert "1-1:1.5.0(00.000*kW)" in first
    assert "1-1:1.5.0(00.123*kW)" in second

    assert protocol.serial is serial_connection


def test_read_does_not_close_connection():
    protocol = IecProtocol()

    serial_connection = Mock()

    serial_connection.read.side_effect = [
        b"\x02" b"1-1:1.5.0(00.000*kW)" b"\x03",
        b"",
    ]

    protocol.serial = serial_connection

    protocol.read()

    serial_connection.close.assert_not_called()
    assert protocol.serial is serial_connection


def test_disconnect_closes_connection():
    protocol = IecProtocol()

    serial_connection = Mock()
    protocol.serial = serial_connection

    protocol.disconnect()

    serial_connection.close.assert_called_once()
    assert protocol.serial is None
