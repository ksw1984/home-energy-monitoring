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


class IecProtocol:
    """Handle the IEC 62056-21 serial communication protocol.

    The protocol starts communication at 300 baud, sends the IEC
    identification request, reads the meter's identification response,
    and determines the baud rate negotiated by the meter.

    After sending the acknowledgement with the negotiated baud rate,
    the initial connection is closed and reopened using the negotiated
    data baud rate.

    The connection remains open after :meth:`connect` and is used by
    :meth:`read` to retrieve meter telegrams until :meth:`disconnect`
    is called.
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

    def _open_serial(self, baud: int) -> serial.Serial:
        """Open the serial connection using IEC serial parameters.

        Args:
            baud: Baud rate for the serial connection.

        Returns:
            An open :class:`serial.Serial` connection.
        """
        return serial.Serial(
            port=self.port,
            baudrate=baud,
            bytesize=serial.SEVENBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def connect(self) -> None:
        """Perform the IEC 62056-21 handshake and open the data connection.

        Communication starts at 300 baud. The meter's identification
        response determines the baud rate used for the actual data
        transmission.

        After the IEC acknowledgement is sent, the initial serial
        connection is closed and reopened at the negotiated baud rate.

        Raises:
            RuntimeError: If the meter does not provide a valid or
                supported baud-rate code.
            serial.SerialException: If the serial connection cannot
                be opened or configured.
        """
        ser = self._open_serial(self.START_BAUD)

        try:
            ser.reset_input_buffer()

            ser.write(self.REQUEST)
            ser.flush()

            identification = self._read_identification(ser)

            baud_code, data_baud = self._get_baud_rate(identification)

            ack = b"\x06" + b"0" + str(baud_code).encode() + b"0\r\n"

            ser.write(ack)
            ser.flush()

            time.sleep(0.2)
        except RuntimeError:
            logger.exception("IEC meter handshake failed. No supported baud-rate code provided.")
        except serial.SerialException:
            logger.exception("IEC serial meter connection failed")
        finally:
            ser.close()

        self.data_baud = data_baud
        self.serial = self._open_serial(data_baud)

    def disconnect(self) -> None:
        """Close the active serial connection.

        Calling this method when no connection is open has no effect.
        """
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def read(self) -> str:
        """Read and extract one IEC meter telegram.

        Reading continues until the telegram's ETX character is received,
        no data has been received for one second, or the overall read
        timeout of 30 seconds is reached.

        Returns:
            The decoded IEC telegram payload without STX and ETX framing.

        Raises:
            RuntimeError: If the protocol is not connected.
        """
        if self.serial is None:
            logger.error("IEC collectors is not connected.")
            raise RuntimeError("IEC collectors is not connected.")

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

            if now - start >= 30.0:
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
        identification = bytearray()
        start = time.monotonic()

        while time.monotonic() - start < timeout:
            chunk = ser.read(64)

            if chunk:
                identification.extend(chunk)

                if b"\r\n" in identification:
                    break

        return bytes(identification)

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
        match = re.search(
            rb"^/[A-Za-z]{3}([0-9])",
            identification,
        )

        if not match:
            logger.error("Could not determine IEC baud rate.")
            raise RuntimeError("Could not determine IEC baud rate.")

        baud_code = int(match.group(1))

        if baud_code not in BAUD_MAP:
            logger.error("Unsupported IEC baud rate code: {baud_code}")
            raise RuntimeError(f"Unsupported IEC baud rate code: {baud_code}")

        return baud_code, BAUD_MAP[baud_code]

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
        if not data:
            return ""

        stx = data.find(b"\x02")

        if stx < 0:
            payload = data
        else:
            payload = data[stx + 1 :]

            etx = payload.find(b"\x03")

            if etx >= 0:
                payload = payload[:etx]

        return payload.decode(
            "latin1",
            errors="replace",
        )
