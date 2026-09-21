from __future__ import annotations

import logging
import re
import time
from typing import ClassVar, TYPE_CHECKING

from src.collectors.definitions.obis import get_obis_definition

import serial
from .hdlc_reader import HDLCReader
from gurux_dlms import GXByteBuffer, GXReplyData
from gurux_dlms.enums import Authentication, InterfaceType, RequestTypes
from gurux_dlms.objects import GXDLMSRegister
from gurux_dlms.secure.GXDLMSSecureClient import GXDLMSSecureClient

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gurux_dlms.objects import GXDLMSObject


logger = logging.getLogger(__name__)

DlmsRequest = bytes | bytearray | list[bytes | bytearray]


class DlmsProtocol:
    """Implement the serial DLMS/COSEM protocol for the meter.

    The protocol performs the following connection sequence:

    1. IEC 62056-21 identification at 300 baud.
    2. IEC mode-C baud-rate negotiation.
    3. HDLC SNRM/UA negotiation.
    4. DLMS AARQ/AARE association.
    5. Association-view loading.

    The resulting DLMS connection remains open and can subsequently be
    used for repeated register reads.

    Args:
        port: Serial device used by the meter.
        client_address: DLMS client address.
        server_address: DLMS server address.
    """

    IEC_BAUDRATE = 300

    IEC_REQUEST = b"/?!\r\n"
    IEC_ACK = b"\x06\x32\x35\x32\r\n"

    BAUD_MAP: ClassVar[dict[int, int]] = {
        0: 300,
        1: 600,
        2: 1200,
        3: 2400,
        4: 4800,
        5: 9600,
        6: 19200,
    }

    REGISTER_SCALER_ATTRIBUTE = 3
    REGISTER_VALUE_ATTRIBUTE = 2

    def __init__(
        self,
        port: str,
        *,
        client_address: int = 16,
        server_address: int = 1,
    ) -> None:
        """Initialize the DLMS protocol handler."""
        self.port = port
        self.client_address = client_address
        self.server_address = server_address

        self.ser: serial.Serial | None = None
        self.hdlc: HDLCReader | None = None
        self.objects = None
        self.connected = False

        self.client = GXDLMSSecureClient(
            useLogicalNameReferencing=False,  # required by meter
            clientAddress=client_address,
            serverAddress=server_address,
            forAuthentication=Authentication.NONE,
            password=None,
            interfaceType=InterfaceType.HDLC,
        )
        self._scalers: dict[str, int] = {}

    def connect(self) -> None:
        """Establish the complete DLMS connection.

        The connection remains open after this method returns.

        Raises:
            Exception: If any stage of the IEC/DLMS connection setup
                fails. The serial connection is closed before the
                exception is propagated.
        """
        if self.connected:
            return

        try:
            baudrate = self._negotiate_baud()
            self._open_dlms_serial(baudrate)
            self._connect_dlms()
            # self._load_association_view()

            self.connected = True

            logger.info(
                "DLMS connection established on %s at %d baud",
                self.port,
                baudrate,
            )

        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """Close the DLMS serial connection and reset connection state."""
        self.connected = False
        self.hdlc = None
        self.objects = None

        if self.ser is None:
            return

        try:
            request = self.client.disconnectRequest()

            if request:
                for frame in self._normalize_requests(request):
                    try:
                        self.ser.write(frame)
                        self.ser.flush()
                    except (serial.SerialException, OSError):
                        logger.debug(
                            "DLMS disconnect request could not be sent",
                            exc_info=True,
                        )
                        break

        except Exception:
            logger.debug(
                "DLMS disconnect request failed",
                exc_info=True,
            )

        finally:
            try:
                self.ser.close()
            except Exception:
                logger.exception(
                    "Error while closing DLMS serial port",
                )
            finally:
                self.ser = None

    def read(self, obis_codes: Iterable[str]) -> dict[str, object]:
        """Read the requested OBIS registers."""
        values: dict[str, object] = {}

        for obis in sorted(obis_codes):
            logger.info("DLMS: reading OBIS %s", obis)

            obj = self._find_object(obis)
            if obj is None:
                continue

            logger.info(
                "DLMS: using object for OBIS %s: class=%s, short_name=0x%04X, logical_name=%s",
                obis,
                obj.objectType,
                obj.shortName,
                obj.logicalName,
            )

            scaler = self._read_scaler(obis, obj)

            requests = self._normalize_requests(
                self.client.read(obj, self.REGISTER_VALUE_ATTRIBUTE),
            )

            logger.info(
                "DLMS: OBIS %s requires %d request frame(s)",
                obis,
                len(requests),
            )

            reply = GXReplyData()

            for request in requests:
                self._send_request(request)

            logger.info("DLMS: waiting for OBIS %s value", obis)

            self._receive_gurux_reply(
                reply=reply,
                timeout=30.0,
            )

            raw_value = reply.value

            logger.info(
                "DLMS: OBIS %s raw value = %s",
                obis,
                raw_value,
            )

            value = raw_value * (10**scaler)

            logger.info(
                "DLMS: OBIS %s scaled value = %s (scaler=10^%d)",
                obis,
                value,
                scaler,
            )

            values[obis] = value

        return values

    def _open_serial(
        self,
        baud: int,
        *,
        data_bits: int,
        parity: str,
    ) -> serial.Serial:
        """Open the meter serial port with the specified parameters.

        Args:
            baud: Serial baud rate.
            data_bits: Number of data bits.
            parity: Serial parity configuration.

        Returns:
            An open ``serial.Serial`` instance.
        """
        return serial.Serial(
            port=self.port,
            baudrate=baud,
            bytesize=data_bits,
            parity=parity,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.2,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

    def _negotiate_baud(self) -> int:
        """Perform IEC 62056-21 identification and baud-rate negotiation."""

        logger.info(
            "DLMS: opening serial port %s at 300 baud, 7E1",
            self.port,
        )

        self.ser = self._open_serial(
            baud=self.IEC_BAUDRATE,
            data_bits=7,
            parity=serial.PARITY_EVEN,
        )

        logger.info("DLMS: serial port opened")

        self.ser.reset_input_buffer()

        logger.info("DLMS: sending IEC identification request /?!")

        self.ser.write(self.IEC_REQUEST)
        self.ser.flush()

        logger.info("DLMS: waiting for IEC identification response")

        identification = self._read_identification()

        logger.info(
            "DLMS: IEC identification response: %r",
            identification,
        )

        if not identification:
            raise RuntimeError(
                "No IEC identification response received",
            )

        baudrate = self._get_baud_rate(identification)

        logger.info(
            "DLMS: negotiated IEC baud rate: %d",
            baudrate,
        )

        logger.info("DLMS: sending IEC ACK")

        self.ser.write(self.IEC_ACK)
        self.ser.flush()

        logger.info(
            "DLMS: IEC ACK sent, waiting for meter to switch baud rate",
        )

        time.sleep(1.0)

        logger.info("DLMS: closing IEC serial connection")

        self.ser.close()
        self.ser = None

        return baudrate

    def _read_identification(
        self,
        timeout: float = 5.0,
    ) -> bytes:
        """Read the IEC 62056-21 identification response.

        The meter may return the identification response in multiple serial
        chunks, so bytes are accumulated until the terminating CRLF is received
        or the timeout expires.

        Args:
            timeout: Maximum time to wait for the identification response.

        Returns:
            Raw IEC identification response.
        """
        logger.info("DLMS: reading IEC identification line")

        identification = bytearray()
        start_time = time.monotonic()

        if self.ser is None:
            raise RuntimeError("Serial connection is not initialized")

        while time.monotonic() - start_time < timeout:
            chunk = self.ser.read(64)

            if chunk:
                logger.info(
                    "DLMS: IEC RX chunk: %r",
                    chunk,
                )
                identification.extend(chunk)

                if b"\r\n" in identification:
                    break

        response = bytes(identification)

        logger.info(
            "DLMS: IEC identification raw response: %r",
            response,
        )

        return response

    def _get_baud_rate(
        self,
        identification: bytes,
    ) -> int:
        """Extract the IEC baud-rate selector from the identification.

        Args:
            identification: Raw IEC identification response.

        Returns:
            Negotiated baud rate.

        Raises:
            serial.SerialException: If the identification does not
                contain a supported baud-rate selector.
        """
        match = re.search(
            rb"^/[A-Za-z]{3}([0-9])",
            identification,
        )

        if match is None:
            raise serial.SerialException(
                f"Could not determine IEC baud rate from {identification!r}",
            )

        selector = int(match.group(1))

        try:
            return self.BAUD_MAP[selector]
        except KeyError as exc:
            raise serial.SerialException(
                f"Unsupported IEC baud-rate selector: {selector}",
            ) from exc

    def _open_dlms_serial(
        self,
        baudrate: int,
    ) -> None:
        """Open the serial port for DLMS communication.

        Args:
            baudrate: Baud rate negotiated during IEC initialization.
        """
        logger.info(
            "DLMS: opening serial port at %d baud, 8N1",
            baudrate,
        )

        self.ser = self._open_serial(
            baudrate,
            data_bits=8,
            parity=serial.PARITY_NONE,
        )

        self.ser.reset_input_buffer()

        logger.info(
            "DLMS: DLMS serial port opened and input buffer reset",
        )

        self.hdlc = HDLCReader(self.ser)

        logger.info(
            "DLMS: HDLC reader initialized",
        )

    def _connect_dlms(self) -> None:  # noqa: C901
        """Establish the HDLC and DLMS application association."""
        if self.ser is None:
            raise serial.SerialException(
                "DLMS serial port is not open",
            )

        if self.hdlc is None:
            raise serial.SerialException(
                "HDLC reader is not initialized",
            )

        # ------------------------------------------------------------
        # SNRM / UA
        # ------------------------------------------------------------

        logger.info("DLMS: sending SNRM request")

        request = self.client.snrmRequest()

        if request:
            self._send_request(request)

            logger.info("DLMS: waiting for SNRM/UA response")

            frame = self.hdlc.read_frame(timeout=30.0)

            if frame is None:
                raise TimeoutError(
                    "Timeout waiting for HDLC frame during SNRM/UA.",
                )

            logger.info(
                "DLMS: received SNRM/UA response (%d bytes)",
                len(frame),
            )

            ua = GXByteBuffer()
            ua.set(frame)

            ua_reply = GXReplyData()

            self.client.getData(
                ua,
                ua_reply,
            )

            if ua_reply.error:
                raise RuntimeError(
                    ua_reply.getErrorMessage(),
                )

            logger.info("DLMS: parsing UA response")

            self.client.parseUAResponse(
                ua_reply.data,
            )

            logger.info("DLMS: SNRM/UA completed")

        # ------------------------------------------------------------
        # AARQ / AARE
        # ------------------------------------------------------------

        logger.info("DLMS: creating AARQ request")

        request = self.client.aarqRequest()

        if request:
            requests = self._normalize_requests(request)

            logger.info(
                "DLMS: sending AARQ request (%d frame(s))",
                len(requests),
            )

            for index, frame_request in enumerate(requests, start=1):
                logger.info(
                    "DLMS: sending AARQ frame %d/%d",
                    index,
                    len(requests),
                )

                self._send_request(frame_request)

                logger.info("DLMS: waiting for AARE response")

                aare_frame = self.hdlc.read_frame(timeout=30.0)

                if aare_frame is None:
                    raise TimeoutError(
                        "Timeout waiting for HDLC frame during AARQ/AARE.",
                    )

                logger.info(
                    "DLMS: received AARE response (%d bytes)",
                    len(aare_frame),
                )

                aare = GXByteBuffer()
                aare.set(aare_frame)

                aare_reply = GXReplyData()

                self.client.getData(
                    aare,
                    aare_reply,
                )

                if aare_reply.error:
                    raise RuntimeError(
                        aare_reply.getErrorMessage(),
                    )

                if aare_reply.data:
                    logger.info("DLMS: parsing AARE response")

                    self.client.parseAareResponse(
                        aare_reply.data,
                    )

                logger.info("DLMS: AARQ/AARE completed")

    def _load_association_view(self) -> None:
        """Load the meter's DLMS association view."""
        logger.info("DLMS: requesting association view")

        request = self.client.getObjectsRequest()

        if not request:
            raise RuntimeError(
                "Gurux returned no association-view request",
            )

        association_reply = GXReplyData()

        logger.info("DLMS: sending association-view request")

        self._send_request(request)

        logger.info("DLMS: waiting for association-view response")

        self._receive_gurux_reply(
            reply=association_reply,
            timeout=30.0,
        )

        logger.info(
            "DLMS: association-view response complete (%d bytes)",
            len(association_reply.data),
        )

        logger.info("DLMS: parsing association view")

        self.objects = self.client.parseObjects(
            association_reply.data,
            onlyKnownObjects=False,
            ignoreInactiveObjects=False,
        )

        logger.info("DLMS: association view loaded")

    def _read_scaler(self, obis: str, obj: GXDLMSObject) -> int:
        """Read and cache the scaler for a DLMS register.

        Attribute 3 of a DLMS register contains the scaler/unit structure.
        The scaler is a base-10 exponent applied to the raw register value.

        Args:
            obis: OBIS code used for logging and caching.
            obj: DLMS register object.

        Returns:
            The decimal scaler exponent.
        """
        cached = self._scalers.get(obis)
        if cached is not None:
            return cached

        logger.info("DLMS: reading scaler for OBIS %s", obis)

        requests = self._normalize_requests(
            self.client.read(obj, self.REGISTER_SCALER_ATTRIBUTE),
        )

        logger.info(
            "DLMS: OBIS %s scaler requires %d request frame(s)",
            obis,
            len(requests),
        )

        reply = GXReplyData()

        for request in requests:
            self._send_request(request)

        logger.info("DLMS: waiting for OBIS %s scaler", obis)

        self._receive_gurux_reply(reply=reply, timeout=30.0)

        scaler_value = reply.value

        logger.info(
            "DLMS: OBIS %s raw scaler response = %r",
            obis,
            scaler_value,
        )

        if isinstance(scaler_value, (list, tuple)) and scaler_value:
            scaler = int(scaler_value[0])
        else:
            raise RuntimeError(f"Unexpected scaler response for OBIS {obis}: {scaler_value!r}")

        self._scalers[obis] = scaler

        logger.info(
            "DLMS: OBIS %s scaler = 10^%d",
            obis,
            scaler,
        )

        return scaler

    def _find_object(self, obis: str) -> GXDLMSObject | None:
        """Find a DLMS object by its configured short name."""

        definition = get_obis_definition(obis)

        if definition is None:
            logger.warning(
                "DLMS: no OBIS definition for %s",
                obis,
            )
            return None

        if definition.dlms_short_name is None:
            logger.warning(
                "DLMS: no short name configured for OBIS %s",
                obis,
            )
            return None

        logger.info(
            "DLMS: OBIS %s -> short_name=0x%04X",
            obis,
            definition.dlms_short_name,
        )

        obj = GXDLMSRegister()
        obj.shortName = definition.dlms_short_name

        if definition.dlms_logical_name is not None:
            obj.logicalName = definition.dlms_logical_name

        logger.info(
            "DLMS: using object for OBIS %s: class=%s, short_name=0x%04X, logical_name=%s",
            obis,
            obj.objectType,
            obj.shortName,
            obj.logicalName,
        )

        return obj

    def _send_request(
        self,
        request: bytes | bytearray,
    ) -> None:
        """Send one DLMS request frame.

        Args:
            request: Raw DLMS/HDLC request frame.

        Raises:
            serial.SerialException: If the serial port is not open.
        """
        if self.ser is None:
            raise serial.SerialException(
                "DLMS serial port is not open",
            )

        request_bytes = bytes(request)

        logger.info(
            "DLMS TX: %s",
            request_bytes.hex(" "),
        )

        self.ser.write(request_bytes)
        self.ser.flush()

    def _receive_gurux_reply(  # noqa: C901
        self,
        *,
        reply: GXReplyData,
        timeout: float = 30.0,
    ) -> GXReplyData:
        """Receive and decode a complete Gurux DLMS response.

        HDLC segmentation, DLMS data blocks and Gurux ``receiverReady()``
        requests are handled until Gurux reports that the response is
        complete.

        Args:
            reply: Gurux reply object used to accumulate the response.
            timeout: Maximum total time in seconds to wait for the
                complete response.

        Returns:
            The complete Gurux reply.

        Raises:
            serial.SerialException: If the serial connection is not open.
            TimeoutError: If no complete response arrives before the
                timeout.
            RuntimeError: If Gurux reports a protocol error or an
                incomplete response.
        """
        if self.ser is None:
            raise serial.SerialException(
                "DLMS serial port is not open",
            )

        if self.hdlc is None:
            raise serial.SerialException(
                "HDLC reader is not initialized",
            )

        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                raise TimeoutError(
                    "Timeout waiting for complete DLMS response.",
                )

            frame = self.hdlc.read_frame(
                timeout=remaining,
            )

            if frame is None:
                raise TimeoutError(
                    "Timeout waiting for HDLC frame.",
                )

            rx = GXByteBuffer()
            rx.set(frame)

            result = self.client.getData(
                rx,
                reply,
            )

            logger.debug(
                "Gurux getData=%s complete=%s moreData=%s",
                result,
                reply.isComplete(),
                reply.moreData,
            )

            if reply.error:
                raise RuntimeError(
                    reply.getErrorMessage(),
                )

            if reply.moreData == RequestTypes.NONE:
                if not reply.isComplete():
                    raise RuntimeError(
                        "Gurux reports no more data, but reply is not complete.",
                    )

                return reply

            ready = self.client.receiverReady(
                reply.moreData,
            )

            if ready is None:
                raise RuntimeError(
                    "Gurux did not generate Receiver Ready.",
                )

            for request in self._normalize_requests(ready):
                self._send_request(request)

    @staticmethod
    def _normalize_requests(
        request: DlmsRequest,
    ) -> list[bytes | bytearray]:
        """Normalize a Gurux request into a list of frames.

        Args:
            request: One request frame or multiple request frames.

        Returns:
            List containing all request frames.
        """
        if isinstance(request, (bytes, bytearray)):
            return [request]

        return list(request)
