import re
import time

import serial

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
    START_BAUD = 300
    REQUEST = b"/?!\r\n"

    def __init__(
        self,
        port="/dev/ttyUSB0",
        timeout=0.2,
    ):
        self.port = port
        self.timeout = timeout
        self.serial = None
        self.data_baud = None

    def _open_serial(self, baud):
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

    def connect(self):
        """
        Perform IEC 62056-21 initialization and leave the
        serial connection open at the negotiated baud rate.
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

        finally:
            ser.close()

        self.data_baud = data_baud
        self.serial = self._open_serial(data_baud)

    def disconnect(self):
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def read(self):
        if self.serial is None:
            raise RuntimeError("IEC collector is not connected.")

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
    def _read_identification(ser, timeout=5.0):
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
    def _get_baud_rate(identification):
        match = re.search(
            rb"^/[A-Za-z]{3}([0-9])",
            identification,
        )

        if not match:
            raise RuntimeError("Could not determine IEC baud rate.")

        baud_code = int(match.group(1))

        if baud_code not in BAUD_MAP:
            raise RuntimeError(f"Unsupported IEC baud rate code: {baud_code}")

        return baud_code, BAUD_MAP[baud_code]

    @staticmethod
    def _extract_payload(data):
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
