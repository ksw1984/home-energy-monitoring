import argparse
import logging
import sys
import time

from src.collectors.iec.iec_protocol import BAUD_MAP

import serial

logger = logging.getLogger("iec-connection-test")


START_BAUD = 300
REQUEST = b"/?!\r\n"
SERIAL_TIMEOUT = 0.2


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def open_serial(port: str, baud: int) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=SERIAL_TIMEOUT,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )


def read_identification(
    ser: serial.Serial,
    timeout: float = 5.0,
) -> bytes:
    identification = bytearray()
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        chunk = ser.read(64)

        if chunk:
            identification.extend(chunk)

            if b"\r\n" in identification:
                break

    return bytes(identification)


def get_baud_rate(
    identification: bytes,
) -> tuple[int, int]:
    match = __import__("re").search(
        rb"^/[A-Za-z]{3}([0-9])",
        identification,
    )

    if not match:
        raise RuntimeError("Could not determine IEC baud rate from identification.")

    baud_code = int(match.group(1))

    if baud_code not in BAUD_MAP:
        raise RuntimeError(f"Unsupported IEC baud rate code: {baud_code}")

    return baud_code, BAUD_MAP[baud_code]


def make_ack(baud_code: int) -> bytes:
    return b"\x060" + str(baud_code).encode("ascii") + b"0\r\n"


def read_data_block(
    ser: serial.Serial,
    quiet_time: float = 1.0,
    max_time: float = 30.0,
) -> bytes:
    data = bytearray()

    start = time.monotonic()
    last_rx = start

    while True:
        chunk = ser.read(256)

        if chunk:
            data.extend(chunk)
            last_rx = time.monotonic()

            if b"\x03" in data:
                time.sleep(0.1)

                remaining = ser.read(64)

                if remaining:
                    data.extend(remaining)

                break

        now = time.monotonic()

        if now - last_rx >= quiet_time:
            break

        if now - start >= max_time:
            break

    return bytes(data)


def perform_iec_session(
    ser: serial.Serial,
    expected_baud: int | None = None,
) -> tuple[int, int, bytes]:
    """
    Perform one complete IEC 62056-21 session on an already
    open physical serial connection.

    The physical Serial object is kept open.

    Protocol:
        300 baud -> /?!
        300 baud -> identification
        300 baud -> ACK
        negotiated baud -> data telegram
    """

    # --------------------------------------------------
    # 1. Switch existing serial connection to 300 baud
    # --------------------------------------------------

    ser.baudrate = START_BAUD

    time.sleep(0.2)

    ser.reset_input_buffer()

    logger.info("Sending /?! at 300 baud")

    ser.write(REQUEST)
    ser.flush()

    # --------------------------------------------------
    # 2. Read identification
    # --------------------------------------------------

    identification = read_identification(ser)

    if not identification:
        raise RuntimeError("No IEC identification response received.")

    logger.info(
        "Identification: %s",
        identification.decode(
            "latin1",
            errors="replace",
        ).strip(),
    )

    # --------------------------------------------------
    # 3. Determine negotiated baud rate
    # --------------------------------------------------

    baud_code, data_baud = get_baud_rate(identification)

    logger.info(
        "Negotiated baud rate: %d baud (code %d)",
        data_baud,
        baud_code,
    )

    if expected_baud is not None and data_baud != expected_baud:
        raise RuntimeError(f"Meter changed negotiated baud rate: expected {expected_baud}, got {data_baud}")

    # --------------------------------------------------
    # 4. Send ACK at 300 baud
    # --------------------------------------------------

    ack = make_ack(baud_code)

    logger.info(
        "Sending ACK at 300 baud: %s",
        ack.hex(" "),
    )

    ser.write(ack)
    ser.flush()

    # --------------------------------------------------
    # 5. Switch SAME physical connection to data baud
    # --------------------------------------------------

    time.sleep(0.2)

    ser.baudrate = data_baud

    logger.info(
        "Switched SAME serial connection to %d baud",
        data_baud,
    )

    # --------------------------------------------------
    # 6. Read data telegram
    # --------------------------------------------------

    logger.info("Reading IEC data telegram...")

    data = read_data_block(
        ser,
        quiet_time=1.0,
        max_time=30.0,
    )

    if not data:
        raise RuntimeError("No IEC data telegram received.")

    return baud_code, data_baud, data


def run_test(
    port: str,
    max_wait: int,
    step: int,
) -> int:

    logger.info("Starting IEC connection lifetime test")
    logger.info("Serial port: %s", port)
    logger.info("Maximum wait: %d seconds", max_wait)
    logger.info("Step: %d seconds", step)

    ser = None

    try:
        # ==================================================
        # Open physical serial connection ONCE
        # ==================================================

        logger.info(
            "Opening physical serial connection at %d baud",
            START_BAUD,
        )

        ser = open_serial(port, START_BAUD)

        # ==================================================
        # Initial IEC session
        # ==================================================

        logger.info("Performing initial IEC handshake...")

        _, data_baud, data = perform_iec_session(ser)

        logger.info(
            "Initial IEC session successful: %d bytes",
            len(data),
        )

        logger.info("Physical serial connection remains open.")

        # ==================================================
        # Increasing idle periods
        # ==================================================

        wait_time = step

        while wait_time <= max_wait:
            logger.info("")
            logger.info("========================================")
            logger.info(
                "WAITING %d SECONDS",
                wait_time,
            )
            logger.info("========================================")

            time.sleep(wait_time)

            logger.info(
                "Starting new IEC session after %d seconds idle time...",
                wait_time,
            )

            try:
                _, _, data = perform_iec_session(
                    ser,
                    expected_baud=data_baud,
                )

            except serial.SerialException:
                logger.exception(
                    "SERIAL CONNECTION LOST after %d seconds idle time.",
                    wait_time,
                )
                return 2

            except RuntimeError as exc:
                logger.error(
                    "IEC SESSION FAILED after %d seconds idle time: %s",
                    wait_time,
                    exc,
                )
                return 3

            logger.info(
                "SUCCESS: IEC session after %d seconds idle time (%d bytes)",
                wait_time,
                len(data),
            )

            wait_time += step

        logger.info("")
        logger.info("========================================")
        logger.info("TEST COMPLETED")
        logger.info(
            "Connection survived %d seconds idle time.",
            max_wait,
        )
        logger.info("========================================")

        return 0

    except serial.SerialException:
        logger.exception("IEC serial communication failed.")
        return 4

    except RuntimeError as exc:
        logger.error(
            "IEC protocol error: %s",
            exc,
        )
        return 5

    except KeyboardInterrupt:
        logger.warning("Test interrupted by user.")
        return 130

    finally:
        if ser is not None:
            logger.info("Closing physical serial connection.")
            ser.close()


def main() -> int:
    configure_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Test IEC 62056-21 session lifetime by keeping "
            "the physical serial connection open and "
            "repeating the IEC handshake after increasing "
            "idle periods."
        )
    )

    parser.add_argument(
        "--port",
        default="/dev/ttyUSB0",
    )

    parser.add_argument(
        "--max-wait",
        type=int,
        default=300,
    )

    parser.add_argument(
        "--step",
        type=int,
        default=1,
    )

    args = parser.parse_args()

    if args.max_wait <= 0:
        parser.error("--max-wait must be greater than 0")

    if args.step <= 0:
        parser.error("--step must be greater than 0")

    return run_test(
        port=args.port,
        max_wait=args.max_wait,
        step=args.step,
    )


if __name__ == "__main__":
    sys.exit(main())
