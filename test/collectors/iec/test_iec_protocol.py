from unittest.mock import Mock, patch

from src.collectors.iec.iec_protocol import IecProtocol

import pytest
import serial


def test_open_serial_uses_iec_serial_settings():
    protocol = IecProtocol(
        port="/dev/ttyUSB0",
        timeout=0.5,
    )

    with patch(
        "src.collectors.iec.iec_protocol.serial.Serial",
    ) as serial_cls:
        serial_connection = protocol._open_serial()

    serial_cls.assert_called_once_with(
        port="/dev/ttyUSB0",
        baudrate=300,
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
    with pytest.raises(RuntimeError, match="Invalid IEC identification"):
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
        side_effect=[0.0, 0.0, 6.0],
    ):
        result = IecProtocol._read_identification(
            serial_connection,
            timeout=5.0,
        )

    assert result == b"/LGZ5"
    serial_connection.read.assert_called_once_with(256)


def test_read_identification_ignores_empty_reads():
    serial_connection = Mock()
    serial_connection.read.side_effect = [
        b"",
        b"/LGZ5\\2ZMD3104107.B40\r\n",
    ]

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[0.0, 0.0, 0.0],
    ):
        result = IecProtocol._read_identification(
            serial_connection,
            timeout=5.0,
        )

    assert result == b"/LGZ5\\2ZMD3104107.B40\r\n"
    assert serial_connection.read.call_count == 2
    serial_connection.read.assert_called_with(256)


def test_read_stops_after_one_second_without_data():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True
    serial_connection.read.return_value = b""

    protocol.serial = serial_connection
    protocol._start_session = Mock()

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[
            0.0,  # start
            1.1,  # now
        ],
    ):
        result = protocol.read()

    assert result == ""

    protocol._start_session.assert_called_once()
    serial_connection.read.assert_called_once_with(256)


def test_read_stops_after_thirty_second_overall_timeout():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True

    serial_connection.read.side_effect = [
        b"some data",
        b"more data",
    ]

    protocol.serial = serial_connection
    protocol._start_session = Mock()

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        side_effect=[
            0.0,
            0.1,
            0.1,
            29.9,
            30.1,
        ],
    ):
        result = protocol.read()

    assert result == "some datamore data"

    protocol._start_session.assert_called_once()
    assert serial_connection.read.call_count == 2


def test_read_reads_remaining_data_after_etx():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True

    serial_connection.read.side_effect = [
        b"\x02payload\x03",
        b"\x00trailing",
    ]

    protocol.serial = serial_connection
    protocol._start_session = Mock()

    with patch(
        "src.collectors.iec.iec_protocol.time.monotonic",
        return_value=0.0,
    ):
        result = protocol.read()

    assert result == "payload"

    protocol._start_session.assert_called_once()

    serial_connection.read.assert_any_call(256)
    serial_connection.read.assert_any_call(64)


def test_extract_payload():
    data = b"\x021-1:1.5.0(00.000*kW)\r\n1-1:2.5.0(08.272*kW)\r\n\x03\x00"

    result = IecProtocol._extract_payload(data)

    assert result == ("1-1:1.5.0(00.000*kW)\r\n1-1:2.5.0(08.272*kW)\r\n")


def test_extract_payload_without_stx():
    data = b"1-1:1.5.0(00.000*kW)\r\n"

    result = IecProtocol._extract_payload(data)

    assert result == "1-1:1.5.0(00.000*kW)\r\n"


def test_extract_payload_without_etx():
    data = b"\x021-1:1.5.0(00.000*kW)\r\n"

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
        match="IEC collector is not connected",
    ):
        protocol.read()


def test_connect_opens_one_serial_connection():
    protocol = IecProtocol(
        port="/dev/ttyUSB0",
    )

    serial_connection = Mock()

    with patch.object(
        protocol,
        "_open_serial",
        return_value=serial_connection,
    ) as open_serial:
        protocol.connect()

    open_serial.assert_called_once()

    assert protocol.serial is serial_connection
    assert protocol.data_baud is None


def test_connect_does_not_open_serial_twice():
    protocol = IecProtocol()

    serial_connection = Mock()
    protocol.serial = serial_connection

    with patch.object(protocol, "_open_serial") as open_serial:
        protocol.connect()

    open_serial.assert_not_called()
    assert protocol.serial is serial_connection


def test_read_uses_300_baud_for_request_and_ack_then_switches_to_data_baud():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True
    serial_connection.baudrate = 300

    events = []

    def write(data):
        events.append(
            ("write", serial_connection.baudrate, data),
        )

    serial_connection.write.side_effect = write
    serial_connection.read.side_effect = [
        b"\x02payload\x03",
        b"",
    ]

    protocol.serial = serial_connection

    protocol._read_identification = Mock(
        return_value=b"/LGZ5\\2ZMD3104107.B40\r\n",
    )

    with patch(
        "src.collectors.iec.iec_protocol.time.sleep",
    ):
        result = protocol.read()

    assert result == "payload"

    assert events == [
        ("write", 300, b"/?!\r\n"),
        ("write", 300, b"\x06050\r\n"),
    ]

    assert serial_connection.baudrate == 9600
    assert protocol.data_baud == 9600


def test_multiple_reads_start_fresh_iec_session():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True
    serial_connection.baudrate = 300

    events = []

    def write(data):
        events.append(
            ("write", serial_connection.baudrate, data),
        )

    serial_connection.write.side_effect = write

    serial_connection.read.side_effect = [
        b"\x02first\x03",
        b"",
        b"\x02second\x03",
        b"",
    ]

    protocol.serial = serial_connection

    protocol._read_identification = Mock(
        side_effect=[
            b"/LGZ5\\2ZMD3104107.B40\r\n",
            b"/LGZ5\\2ZMD3104107.B40\r\n",
        ],
    )

    with patch(
        "src.collectors.iec.iec_protocol.time.sleep",
    ):
        first = protocol.read()
        second = protocol.read()

    assert first == "first"
    assert second == "second"

    assert events == [
        ("write", 300, b"/?!\r\n"),
        ("write", 300, b"\x06050\r\n"),
        ("write", 300, b"/?!\r\n"),
        ("write", 300, b"\x06050\r\n"),
    ]

    assert protocol._read_identification.call_count == 2

    # Same physical serial object throughout.
    assert protocol.serial is serial_connection

    # Each session ends at the negotiated data baud.
    assert serial_connection.baudrate == 9600


def test_read_does_not_close_connection():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True

    serial_connection.read.side_effect = [
        b"\x02payload\x03",
        b"",
    ]

    protocol.serial = serial_connection

    protocol._read_identification = Mock(return_value=b"/LGZ5\\2ZMD3104107.B40\r\n")

    with patch("src.collectors.iec.iec_protocol.time.sleep"):
        protocol.read()

    assert protocol.serial is serial_connection
    serial_connection.close.assert_not_called()


def test_disconnect_closes_connection():
    protocol = IecProtocol()

    serial_connection = Mock()
    protocol.serial = serial_connection

    protocol.disconnect()

    serial_connection.close.assert_called_once()
    assert protocol.serial is None


def test_start_session_resets_input_buffer_before_request():
    protocol = IecProtocol()

    serial_connection = Mock()
    serial_connection.is_open = True

    protocol.serial = serial_connection

    protocol._read_identification = Mock(
        return_value=b"/LGZ5\\2ZMD3104107.B40\r\n",
    )

    with patch(
        "src.collectors.iec.iec_protocol.time.sleep",
    ):
        protocol._start_session()

    serial_connection.reset_input_buffer.assert_called_once()

    serial_connection.write.assert_any_call(b"/?!\r\n")
    serial_connection.write.assert_any_call(b"\x06050\r\n")

    assert serial_connection.baudrate == 9600
