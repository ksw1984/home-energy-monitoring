import logging
import re
import time

import serial

logger = logging.getLogger(__name__)


BAUD_MAP = {
    0: 300,
    1: 600,
    2: 1200,
    3: 2400,
    4: 4800,
    5: 9600,
    6: 19200,
}
READ_TIMEOUT_SECONDS = 30.0


class IecProtocol:
    """Handle the IEC 62056-21 serial communication protocol.

    The physical serial connection is opened once and remains open until
    :meth:`disconnect` is called.

    Each :meth:`read` starts a fresh IEC protocol session on the same
    physical serial connection. The serial baud rate is switched to
    300 baud for the identification request and acknowledgement, then
    switched to the negotiated data baud rate for the meter telegram.
    """

    START_BAUD = 300
    REQUEST = b"/?!\r\n"

    def __init__(
        self,
        port="/dev/ttyUSB0",
        timeout=0.2,
    ) -> None:
        """Initialize the IEC serial protocol handler.

        Args:
            port: Serial device used to communicate with the meter.
            timeout: Serial read timeout in seconds.
        """
        self.port = port
        self.timeout = timeout
        self.data_baud: int | None = None
        self.serial: serial.Serial | None = None

    def _open_serial(self) -> serial.Serial:
        """Open the serial connection using IEC serial parameters.

        Returns:
            An open :class:`serial.Serial` connection.

        Raises:
            serial.SerialException: If the serial connection cannot be
                opened.
        """
        return serial.Serial(
            port=self.port,
            baudrate=self.START_BAUD,
            bytesize=serial.SEVENBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def connect(self) -> None:
        """Open the physical serial connection.

        The IEC protocol handshake is performed by read().
        """
        if self.serial is not None and self.serial.is_open:
            return

        self.serial = self._open_serial()

        logger.info("IEC serial port opened on %s", self.port)

    def disconnect(self) -> None:
        """Close the active serial connection.

        Calling this method when no connection is open has no effect.
        """
        if self.serial is not None:
            self.serial.close()
            self.serial = None

        self.data_baud = None

    def _start_session(self) -> None:
        """Start a fresh IEC 62056-21 protocol session.

        The same physical Serial object is reused. The baud rate is
        switched back to 300 baud before /?! and the ACK are sent.
        """
        if self.serial is None or not self.serial.is_open:
            raise RuntimeError("IEC collector is not connected.")

        ser = self.serial

        # IEC identification always starts at 300 baud.
        ser.baudrate = self.START_BAUD
        ser.reset_input_buffer()

        logger.debug("Sending IEC identification request at 300 baud")

        ser.write(self.REQUEST)
        ser.flush()

        identification = self._read_identification(ser)

        baud_code, data_baud = self._get_baud_rate(identification)

        ack = b"\x06" + b"0" + str(baud_code).encode() + b"0\r\n"

        logger.debug(
            "Sending IEC ACK at 300 baud, switching to %d baud",
            data_baud,
        )

        ser.write(ack)
        ser.flush()

        time.sleep(0.2)

        # IMPORTANT:
        # Keep the same physical serial connection and only change baud.
        ser.baudrate = data_baud

        self.data_baud = data_baud

    def read(self) -> str:
        """Start a fresh IEC session and read one data telegram."""
        if self.serial is None or not self.serial.is_open:
            raise RuntimeError("IEC collector is not connected.")

        self._start_session()

        data = bytearray()
        start = time.monotonic()
        last_rx = start

        while True:
            chunk = self.serial.read(256)

            if chunk:
                data.extend(chunk)
                last_rx = time.monotonic()

                if b"\x03" in data:
                    time.sleep(0.1)

                    remaining = self.serial.read(64)

                    if remaining:
                        data.extend(remaining)

                    break

            now = time.monotonic()

            if now - last_rx >= 1.0:
                break

            if now - start >= READ_TIMEOUT_SECONDS:
                break

        return self._extract_payload(bytes(data))

    @staticmethod
    def _read_identification(
        ser: serial.Serial,
        timeout: float = 5.0,
    ) -> bytes:
        """Read the meter identification response.

        Args:
            ser: Open serial connection using the initial baud rate.
            timeout: Maximum time to wait for the identification response.

        Returns:
            Raw identification response from the meter.
        """
        data = bytearray()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            chunk = ser.read(256)

            if chunk:
                data.extend(chunk)

                if b"\r\n" in data:
                    break

        if not data:
            raise RuntimeError("No IEC identification response received.")

        return bytes(data)

    @staticmethod
    def _get_baud_rate(
        identification: bytes,
    ) -> tuple[int, int]:
        """Extract the negotiated baud-rate code from meter identification.

        The IEC identification response contains a single digit indicating
        the baud rate to use for subsequent data communication.

        Args:
            identification: Raw identification response from the meter.

        Returns:
            A tuple containing the IEC baud-rate code and the corresponding
            baud rate in bits per second.

        Raises:
            RuntimeError: If no valid or supported baud-rate code is found.
        """
        match = re.search(rb"^/[A-Za-z]{3}([0-9])", identification)

        if match is None:
            raise RuntimeError(f"Invalid IEC identification: {identification!r}")

        baud_code = int(match.group(1))

        try:
            data_baud = BAUD_MAP[baud_code]
        except KeyError:
            logger.error(
                "Unsupported IEC baud rate code: %d",
                baud_code,
            )
            raise RuntimeError(f"Unsupported IEC baud rate code: {baud_code}") from None

        return baud_code, data_baud

    @staticmethod
    def _extract_payload(data: bytes) -> str:
        """Remove IEC telegram framing and decode the payload.

        The IEC telegram may contain STX and ETX framing characters.
        Everything before STX and after ETX is ignored.

        Args:
            data: Raw bytes received from the meter.

        Returns:
            The decoded telegram payload. Latin-1 decoding is used because
            IEC meter telegrams are byte-oriented and may contain characters
            outside standard ASCII.
        """
        if b"\x02" in data:
            data = data.split(b"\x02", 1)[1]

        if b"\x03" in data:
            data = data.split(b"\x03", 1)[0]

        return data.decode("latin1")
