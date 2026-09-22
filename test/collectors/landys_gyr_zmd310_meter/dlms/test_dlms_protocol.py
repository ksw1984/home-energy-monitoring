from unittest.mock import Mock

from src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol import (
    DlmsProtocol,
)

import pytest
import serial
from gurux_dlms.enums import RequestTypes


@pytest.fixture
def protocol():
    return DlmsProtocol("/dev/ttyUSB0")


def test_get_baud_rate():
    protocol = DlmsProtocol("/dev/ttyUSB0")

    assert protocol._get_baud_rate(b"/LGZ5\r\n") == 9600
    assert protocol._get_baud_rate(b"/LGZ6\r\n") == 19200
    assert protocol._get_baud_rate(b"/LGZ0\r\n") == 300


def test_get_baud_rate_rejects_invalid_identification(protocol):
    with pytest.raises(
        serial.SerialException,
        match="Could not determine IEC baud rate",
    ):
        protocol._get_baud_rate(b"invalid")


def test_get_baud_rate_rejects_unsupported_selector(protocol):
    with pytest.raises(
        serial.SerialException,
        match="Unsupported IEC baud-rate selector: 9",
    ):
        protocol._get_baud_rate(b"/LGZ9\r\n")


def test_normalize_requests_single_bytes():
    request = b"\x01\x02"

    assert DlmsProtocol._normalize_requests(request) == [request]


def test_normalize_requests_single_bytearray():
    request = bytearray(b"\x01\x02")

    assert DlmsProtocol._normalize_requests(request) == [request]


def test_normalize_requests_multiple_frames():
    requests = [
        b"\x01\x02",
        bytearray(b"\x03\x04"),
    ]

    assert DlmsProtocol._normalize_requests(requests) == requests


def test_open_serial(monkeypatch, protocol):
    serial_instance = Mock()

    serial_class = Mock(return_value=serial_instance)

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.serial.Serial",
        serial_class,
    )

    result = protocol._open_serial(
        9600,
        data_bits=8,
        parity=serial.PARITY_NONE,
    )

    assert result is serial_instance

    serial_class.assert_called_once_with(
        port="/dev/ttyUSB0",
        baudrate=9600,
        bytesize=8,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.2,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )


def test_read_identification(protocol):
    protocol.ser = Mock()

    protocol.ser.read.side_effect = [
        b"/LGZ5",
        b"\r\n",
    ]

    result = protocol._read_identification()

    assert result == b"/LGZ5\r\n"
    assert protocol.ser.read.call_count == 2


def test_read_identification_stops_after_complete_line(protocol):
    protocol.ser = Mock()
    protocol.ser.read.return_value = b"/LGZ5\r\n"

    result = protocol._read_identification()

    assert result == b"/LGZ5\r\n"
    protocol.ser.read.assert_called_once_with(64)


def test_read_identification_requires_serial(protocol):
    with pytest.raises(
        RuntimeError,
        match="Serial connection is not initialized",
    ):
        protocol._read_identification()


def test_send_request(protocol):
    protocol.ser = Mock()

    protocol._send_request(b"\x01\x02\x03")

    protocol.ser.write.assert_called_once_with(
        b"\x01\x02\x03",
    )
    protocol.ser.flush.assert_called_once()


def test_send_request_accepts_bytearray(protocol):
    protocol.ser = Mock()

    protocol._send_request(bytearray(b"\x01\x02"))

    protocol.ser.write.assert_called_once_with(b"\x01\x02")


def test_send_request_requires_serial(protocol):
    with pytest.raises(
        serial.SerialException,
        match="DLMS serial port is not open",
    ):
        protocol._send_request(b"\x01")


def test_find_object(protocol, monkeypatch):
    definition = Mock(
        dlms_short_name=0x3E18,
        dlms_logical_name="1.1.1.6.0.255",
    )

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=definition),
    )

    obj = protocol._find_object("1.6.0")

    assert obj is not None
    assert obj.shortName == 0x3E18
    assert obj.logicalName == "1.1.1.6.0.255"


def test_find_object_returns_none_without_definition(
    protocol,
    monkeypatch,
):
    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=None),
    )

    assert protocol._find_object("1.6.0") is None


def test_find_object_returns_none_without_short_name(
    protocol,
    monkeypatch,
):
    definition = Mock(
        dlms_short_name=None,
        dlms_logical_name="1.1.1.6.0.255",
    )

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=definition),
    )

    assert protocol._find_object("1.6.0") is None


def test_find_object_without_logical_name(
    protocol,
    monkeypatch,
):
    definition = Mock(
        dlms_short_name=0x3E18,
        dlms_logical_name=None,
    )

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=definition),
    )

    obj = protocol._find_object("1.6.0")

    assert obj is not None
    assert obj.shortName == 0x3E18


def test_read_scaler_reads_and_caches(protocol):
    obj = Mock()
    protocol.client.read = Mock(return_value=b"\x01")
    protocol._send_request = Mock()

    def receive(*, reply, timeout):
        reply.value = [-1]
        return reply

    protocol._receive_gurux_reply = Mock(side_effect=receive)

    assert protocol._read_scaler("1.6.0", obj) == -1
    assert protocol._read_scaler("1.6.0", obj) == -1

    protocol.client.read.assert_called_once_with(
        obj,
        protocol.REGISTER_SCALER_ATTRIBUTE,
    )


def test_read_scaler_rejects_invalid_response(protocol):
    obj = Mock()
    protocol.client.read = Mock(return_value=b"\x01")
    protocol._send_request = Mock()

    def receive(*, reply, timeout):
        reply.value = 123
        return reply

    protocol._receive_gurux_reply = Mock(side_effect=receive)

    with pytest.raises(RuntimeError, match="Unexpected scaler response"):
        protocol._read_scaler("1.6.0", obj)


def test_receive_gurux_reply(protocol):
    protocol.ser = Mock()
    protocol.hdlc = Mock()

    frame = b"\x7e\x01\x02\x7e"

    protocol.hdlc.read_frame.return_value = frame

    reply = Mock()
    reply.error = False
    reply.moreData = 0
    reply.isComplete.return_value = True

    protocol.client.getData = Mock()

    result = protocol._receive_gurux_reply(
        reply=reply,
        timeout=5.0,
    )

    assert result is reply
    protocol.hdlc.read_frame.assert_called_once()


def test_receive_gurux_reply_requires_serial(protocol):
    protocol.hdlc = Mock()

    with pytest.raises(
        serial.SerialException,
        match="DLMS serial port is not open",
    ):
        protocol._receive_gurux_reply(
            reply=Mock(),
        )


def test_receive_gurux_reply_requires_hdlc(protocol):
    protocol.ser = Mock()

    with pytest.raises(
        serial.SerialException,
        match="HDLC reader is not initialized",
    ):
        protocol._receive_gurux_reply(
            reply=Mock(),
        )


def test_receive_gurux_reply_times_out(protocol):
    protocol.ser = Mock()
    protocol.hdlc = Mock()
    protocol.hdlc.read_frame.return_value = None

    with pytest.raises(
        TimeoutError,
        match="Timeout waiting for HDLC frame",
    ):
        protocol._receive_gurux_reply(
            reply=Mock(),
            timeout=1.0,
        )


def test_receive_gurux_reply_raises_gurux_error(protocol):
    protocol.ser = Mock()
    protocol.hdlc = Mock()
    protocol.hdlc.read_frame.return_value = b"\x7e"

    reply = Mock()
    reply.error = True
    reply.getErrorMessage.return_value = "DLMS error"

    protocol.client.getData = Mock()

    with pytest.raises(RuntimeError, match="DLMS error"):
        protocol._receive_gurux_reply(
            reply=reply,
        )


def test_receive_gurux_reply_handles_more_data(protocol):
    protocol.ser = Mock()
    protocol.hdlc = Mock()
    protocol.hdlc.read_frame.side_effect = [
        b"\x7e\x01\x7e",
        b"\x7e\x02\x7e",
    ]

    reply = Mock()
    reply.error = False
    reply.moreData = RequestTypes.DATABLOCK

    complete_calls = 0

    def is_complete():
        nonlocal complete_calls
        complete_calls += 1
        return complete_calls >= 2

    reply.isComplete.side_effect = is_complete

    get_data_calls = 0

    def get_data(rx, current_reply):
        nonlocal get_data_calls
        get_data_calls += 1

        if get_data_calls == 1:
            current_reply.moreData = RequestTypes.DATABLOCK
        else:
            current_reply.moreData = RequestTypes.NONE

        return True

    protocol.client.getData = Mock(side_effect=get_data)

    receiver_ready = Mock(return_value=b"\xaa\xbb")
    protocol.client.receiverReady = receiver_ready

    protocol._send_request = Mock()

    result = protocol._receive_gurux_reply(
        reply=reply,
        timeout=5.0,
    )

    assert result is reply
    assert get_data_calls == 2
    receiver_ready.assert_called_once_with(RequestTypes.DATABLOCK)
    protocol._send_request.assert_called_once_with(b"\xaa\xbb")


def test_read_returns_values(protocol, monkeypatch):
    definition = Mock(
        dlms_short_name=0x3E18,
        dlms_logical_name="1.1.1.6.0.255",
    )

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=definition),
    )

    protocol._read_scaler = Mock(return_value=-3)
    protocol.client.read = Mock(return_value=b"\x01\x02")
    protocol._send_request = Mock()

    def receive(*, reply, timeout):
        reply.value = 1234
        return reply

    protocol._receive_gurux_reply = Mock(side_effect=receive)

    result = protocol.read({"1.6.0"})

    assert result == {"1.6.0": 1.234}

    read_args = protocol.client.read.call_args
    assert read_args.args[0].shortName == 0x3E18
    assert read_args.args[1] == protocol.REGISTER_VALUE_ATTRIBUTE

    protocol._send_request.assert_called_once_with(b"\x01\x02")


def test_read_skips_unknown_obis(protocol, monkeypatch):
    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=None),
    )

    assert protocol.read({"1.6.0"}) == {}


def test_read_sends_value_request(protocol, monkeypatch):
    definition = Mock(
        dlms_short_name=0x3E18,
        dlms_logical_name="1.1.1.6.0.255",
    )

    monkeypatch.setattr(
        "src.collectors.landys_gyr_zmd310_meter.dlms.dlms_protocol.get_obis_definition",
        Mock(return_value=definition),
    )

    protocol._read_scaler = Mock(return_value=0)
    protocol.client.read = Mock(return_value=b"\x01\x02")
    protocol._send_request = Mock()

    def receive(*, reply, timeout):
        reply.value = 123
        return reply

    protocol._receive_gurux_reply = Mock(side_effect=receive)

    result = protocol.read({"1.6.0"})

    assert result == {"1.6.0": 123}

    read_args = protocol.client.read.call_args
    assert read_args.args[1] == protocol.REGISTER_VALUE_ATTRIBUTE
    protocol._send_request.assert_called_once_with(b"\x01\x02")


def test_disconnect_closes_serial(protocol):
    ser = Mock()
    protocol.ser = ser
    protocol.client.disconnectRequest = Mock(return_value=b"\x01\x02")

    protocol.disconnect()

    ser.write.assert_called_once_with(b"\x01\x02")
    ser.flush.assert_called_once()
    ser.close.assert_called_once()
    assert protocol.ser is None


def test_disconnect_without_serial(protocol):
    protocol.connected = True
    protocol.hdlc = Mock()
    protocol.objects = Mock()

    protocol.disconnect()

    assert not protocol.connected
    assert protocol.ser is None
    assert protocol.hdlc is None
    assert protocol.objects is None


def test_connect_returns_when_already_connected(protocol):
    protocol.connected = True

    protocol._negotiate_baud = Mock()

    protocol.connect()

    protocol._negotiate_baud.assert_not_called()


def test_connect_establishes_connection(protocol):
    protocol._negotiate_baud = Mock(return_value=9600)
    protocol._open_dlms_serial = Mock()
    protocol._connect_dlms = Mock()

    protocol.connect()

    protocol._negotiate_baud.assert_called_once()
    protocol._open_dlms_serial.assert_called_once_with(9600)
    protocol._connect_dlms.assert_called_once()

    assert protocol.connected is True


def test_connect_disconnects_when_setup_fails(protocol):
    protocol._negotiate_baud = Mock(
        side_effect=RuntimeError("failed"),
    )
    protocol.disconnect = Mock()

    with pytest.raises(RuntimeError, match="failed"):
        protocol.connect()

    protocol.disconnect.assert_called_once()
    assert protocol.connected is False
