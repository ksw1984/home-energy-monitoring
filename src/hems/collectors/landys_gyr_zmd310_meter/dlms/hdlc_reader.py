from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import serial


class HDLCReader:
    """Read complete HDLC frames from a serial connection.

    The reader maintains an internal receive buffer because a single
    ``serial.Serial.read()`` call may contain a partial frame, multiple
    frames, or data belonging to the next frame.

    HDLC frames are delimited by ``0x7E``. The frame length is taken from
    the HDLC length field in bytes 1 and 2 of the frame header.

    Args:
        ser: Open serial connection used to receive HDLC frames.
    """

    FRAME_FLAG = 0x7E
    MIN_FRAME_LENGTH = 2
    LENGTH_FIELD_SIZE = 3
    READ_SIZE = 256

    def __init__(self, ser: serial.Serial) -> None:
        """Initialize the HDLC reader.

        Args:
            ser: Open serial connection.
        """
        self.ser = ser
        self.buffer = bytearray()

    def read_frame(self, timeout: float = 5.0) -> bytes | None:
        """Read one complete HDLC frame.

        The method waits until a complete frame is available or the
        specified timeout expires.

        Args:
            timeout: Maximum number of seconds to wait for a complete
                frame.

        Returns:
            The complete HDLC frame including both ``0x7E`` delimiters,
            or ``None`` if no complete frame was received before the
            timeout expired.
        """
        start_time = time.monotonic()

        while time.monotonic() - start_time < timeout:
            try:
                start_index = self.buffer.index(
                    self.FRAME_FLAG,
                )
            except ValueError:
                start_index = -1

            if start_index >= 0:
                if start_index:
                    del self.buffer[:start_index]

                if len(self.buffer) >= self.LENGTH_FIELD_SIZE:
                    frame_length = ((self.buffer[1] & 0x07) << 8) | self.buffer[2]

                    total_length = frame_length + self.MIN_FRAME_LENGTH

                    if frame_length < self.MIN_FRAME_LENGTH:
                        del self.buffer[:1]
                        continue

                    if len(self.buffer) >= total_length:
                        frame = bytes(
                            self.buffer[:total_length],
                        )

                        del self.buffer[:total_length]

                        if frame[-1] != self.FRAME_FLAG:
                            continue

                        return frame

            chunk = self.ser.read(
                self.READ_SIZE,
            )

            if chunk:
                self.buffer.extend(chunk)

        return None
