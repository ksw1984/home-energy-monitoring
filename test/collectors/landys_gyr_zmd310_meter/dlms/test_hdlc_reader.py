from unittest.mock import Mock

from src.collectors.landys_gyr_zmd310_meter.dlms.hdlc_reader import HDLCReader


def test_read_frame_reads_complete_frame():
    frame = bytes.fromhex("7e a0 05 01 02 03 7e")

    ser = Mock()
    ser.read.return_value = frame

    reader = HDLCReader(ser)

    assert reader.read_frame() == frame


def test_read_frame_handles_partial_reads():
    frame = bytes.fromhex("7e a0 05 01 02 03 7e")

    ser = Mock()
    ser.read.side_effect = [
        frame[:4],
        frame[4:],
    ]

    reader = HDLCReader(ser)

    assert reader.read_frame() == frame


def test_read_frame_handles_multiple_frames():
    frame1 = bytes.fromhex("7e a0 05 01 02 03 7e")
    frame2 = bytes.fromhex("7e a0 05 04 05 06 7e")

    ser = Mock()
    ser.read.return_value = frame1 + frame2

    reader = HDLCReader(ser)

    assert reader.read_frame() == frame1
    assert reader.read_frame() == frame2


def test_read_frame_discards_bytes_before_frame():
    frame = bytes.fromhex("7e a0 05 01 02 03 7e")

    ser = Mock()
    ser.read.return_value = b"\x00\x11\x22" + frame

    reader = HDLCReader(ser)

    assert reader.read_frame() == frame


def test_read_frame_returns_none_on_timeout():
    ser = Mock()
    ser.read.return_value = b""

    reader = HDLCReader(ser)

    assert reader.read_frame(timeout=0.001) is None


def test_read_frame_ignores_invalid_length():
    ser = Mock()
    ser.read.return_value = bytes.fromhex("7e a0 01 7e")

    reader = HDLCReader(ser)

    assert reader.read_frame(timeout=0.001) is None


def test_read_frame_rejects_frame_without_end_flag():
    ser = Mock()
    ser.read.return_value = bytes.fromhex("7e a0 05 01 02 03 00")

    reader = HDLCReader(ser)

    assert reader.read_frame(timeout=0.001) is None


def test_read_frame_uses_length_field_not_next_flag():
    frame = bytes.fromhex("7e a0 05 7e 01 02 7e")

    ser = Mock()
    ser.read.return_value = frame

    reader = HDLCReader(ser)

    assert reader.read_frame() == frame
