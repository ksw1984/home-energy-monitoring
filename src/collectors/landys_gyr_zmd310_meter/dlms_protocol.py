from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from src.collectors.definitions.obis import CURRENT_OBIS

import serial
from .hdlc_reader import HDLCReader
from gurux_dlms import GXByteBuffer, GXReplyData
from gurux_dlms.enums import Authentication, InterfaceType, RequestTypes
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
    2. IEC mode-C baud-rate negotiation to 9600 baud.
    3. HDLC SNRM/UA negotiation.
    4. DLMS AARQ/AARE association.
    5. Association-view loading.
    6. Register scaler/unit loading.

    The resulting DLMS connection remains open and can subsequently be
    used for repeated register reads.

    Args:
        port: Serial device used by the meter.
        client_address: DLMS client address.
        server_address: DLMS server address.
    """

    IEC_BAUDRATE = 300
    DLMS_BAUDRATE = 9600

    REGISTER_VALUE_ATTRIBUTE = 2
    REGISTER_SCALER_ATTRIBUTE = 3

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

        self.client = GXDLMSSecureClient(
            useLogicalNameReferencing=False,
            clientAddress=client_address,
            serverAddress=server_address,
            forAuthentication=Authentication.NONE,
            password=None,
            interfaceType=InterfaceType.HDLC,
        )

        self.connected = False

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
            self._negotiate_baud()
            self._connect_dlms()
            self._load_association_view()
            self._load_scalers()

            self.connected = True

            logger.info(
                "DLMS connection established on %s",
                self.port,
            )

        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """Close the DLMS serial connection and reset connection state."""
        self.connected = False

        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                logger.exception(
                    "Error while closing DLMS serial port",
                )
            finally:
                self.ser = None

        self.hdlc = None

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

    def _negotiate_baud(self) -> None:
        """Perform the IEC 62056-21 identification and baud negotiation.

        The meter is initially contacted at 300 baud using 7E1. The
        proven meter-specific ACK then requests communication at
        9600 baud.
        """
        ser = self._open_serial(
            self.IEC_BAUDRATE,
            data_bits=7,
            parity=serial.PARITY_EVEN,
        )

        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            ser.write(b"/?!\r\n")
            ser.flush()

            response = ser.read_until(b"\r\n")

            if not response:
                raise serial.SerialException(
                    "No IEC identification response",
                )

            logger.debug(
                "IEC identification: %r",
                response,
            )

            ser.write(b"\x06\x32\x35\x32\r\n")
            ser.flush()

        finally:
            ser.close()

    def _connect_dlms(self) -> None:
        """Establish the HDLC and DLMS application association."""
        self.ser = self._open_serial(
            self.DLMS_BAUDRATE,
            data_bits=8,
            parity=serial.PARITY_NONE,
        )

        self.hdlc = HDLCReader(self.ser)

        # ------------------------------------------------------------
        # SNRM
        # ------------------------------------------------------------

        request = self.client.snrmRequest()

        if request:
            self.ser.write(request)
            self.ser.flush()

            reply = self._receive_gurux_reply(
                timeout=30.0,
            )

            self.client.parseUAResponse(
                reply.data,
            )

        # ------------------------------------------------------------
        # AARQ
        # ------------------------------------------------------------

        request = self.client.aarqRequest()

        if request:
            for frame in request:
                self.ser.write(frame)
                self.ser.flush()

                reply = self._receive_gurux_reply(
                    timeout=30.0,
                )

                if reply.data:
                    self.client.parseAareResponse(
                        reply.data,
                    )

    def _load_association_view(self) -> None:
        """Load the meter's DLMS association view."""
        reply = GXReplyData()

        request = self.client.getObjectsRequest()

        if not request:
            raise RuntimeError(
                "Gurux returned no association-view request",
            )

        self._send_and_receive(
            request,
            reply,
            timeout=30.0,
        )

        self.client.parseObjects(
            reply.data,
        )

        logger.info(
            "DLMS association view loaded: %d objects",
            len(self.client.objects),
        )

    def _load_scalers(self) -> None:
        """Read scaler and unit information for current OBIS registers.

        DLMS register attribute 3 contains the scaler and unit metadata.
        This information is loaded once during connection setup.
        """

        for obis in self._current_obis():
            obj = self._find_object(obis)

            if obj is None:
                logger.warning(
                    "DLMS object not found while loading scaler: %s",
                    obis,
                )
                continue

            try:
                if obj.getAttributeCount() < self.REGISTER_SCALER_ATTRIBUTE:
                    continue

                if not obj.canRead(self.REGISTER_SCALER_ATTRIBUTE):
                    continue

                # Gurux updates the object when updateValue()
                # is called after reading attribute 3.
                self._read_and_update(
                    obj=obj,
                    attribute=self.REGISTER_SCALER_ATTRIBUTE,
                )

                logger.debug(
                    "DLMS scaler loaded: %s scaler=%s unit=%s",
                    obis,
                    getattr(obj, "scaler", None),
                    getattr(obj, "unit", None),
                )

            except Exception:
                logger.exception(
                    "Failed to read scaler for %s",
                    obis,
                )

    def _current_obis(self) -> Iterable[str]:
        """Return the OBIS codes configured for current measurements.

        Returns:
            The shared ``CURRENT_OBIS`` collection.
        """
        return CURRENT_OBIS

    def read(
        self,
        obis_codes: Iterable[str],
    ) -> dict[str, float]:
        """Read register values for the requested OBIS codes.

        Args:
            obis_codes: OBIS codes to read.

        Returns:
            Mapping of OBIS code to numeric register value.

        Raises:
            serial.SerialException: If no DLMS connection exists.
        """
        if not self.connected:
            raise serial.SerialException(
                "DLMS connection is not established",
            )

        result: dict[str, float] = {}

        for obis in obis_codes:
            obj = self._find_object(obis)

            if obj is None:
                logger.warning(
                    "DLMS object not found: %s",
                    obis,
                )
                continue

            value = self._read_and_update(
                obj=obj,
                attribute=self.REGISTER_VALUE_ATTRIBUTE,
            )

            if value is None:
                continue

            result[obis] = float(value)

        return result

    @staticmethod
    def _obis_to_logical_name(obis: str) -> str:
        """Convert a three-part OBIS code to its DLMS logical name.

        Args:
            obis: OBIS code such as ``16.7.0``.

        Returns:
            DLMS logical name such as ``1.1.16.7.0.255``.

        Raises:
            ValueError: If the OBIS code does not contain exactly three
                components.
        """
        parts = obis.split(".")
        max_parts = 3

        if len(parts) != max_parts:
            raise ValueError(
                f"Invalid OBIS code: {obis}",
            )

        return f"1.1.{obis}.255"

    def _find_object(
        self,
        obis: str,
    ) -> GXDLMSObject | None:
        """Find a Gurux object by its OBIS logical name.

        Args:
            obis: Three-part OBIS code.

        Returns:
            Matching Gurux object or ``None`` if it is not present in the
            association view.
        """
        logical_name = self._obis_to_logical_name(obis)

        return self.client.objects.findByLN(
            None,
            logical_name,
        )

    def _read_and_update(
        self,
        obj: GXDLMSObject,
        attribute: int,
    ) -> Any:
        """Read one DLMS object attribute and update the Gurux object.

        Args:
            obj: Gurux DLMS object to read.
            attribute: Object attribute index.

        Returns:
            The value returned by ``GXDLMSSecureClient.updateValue()``.
            ``None`` is returned when Gurux does not generate a request.
        """
        request = self.client.read(
            obj,
            attribute,
        )

        if not request:
            return None

        reply = GXReplyData()

        self._send_and_receive(
            request,
            reply,
            timeout=30.0,
        )

        return self.client.updateValue(
            obj,
            attribute,
            reply.value,
        )

    def _send_and_receive(
        self,
        request: DlmsRequest,
        reply: GXReplyData,
        *,
        timeout: float,
    ) -> GXReplyData:
        """Send DLMS request frames and receive the complete response.

        Args:
            request: One request frame or multiple request frames.
            reply: Gurux reply object used to accumulate the response.
            timeout: Maximum time in seconds to wait for the response.

        Returns:
            The supplied, populated ``GXReplyData`` instance.

        Raises:
            serial.SerialException: If the serial port is not open.
        """
        if self.ser is None:
            raise serial.SerialException(
                "Serial port is not open",
            )

        requests = [request] if isinstance(request, (bytes, bytearray)) else request

        for frame in requests:
            self.ser.write(frame)
            self.ser.flush()

        return self._receive_gurux_reply(
            reply=reply,
            timeout=timeout,
        )

    def _receive_gurux_reply(  # noqa: C901, PLR0912
        self,
        *,
        reply: GXReplyData | None = None,
        timeout: float = 30.0,
    ) -> GXReplyData:
        """Receive and decode a complete Gurux DLMS response.

        HDLC segmentation, DLMS data blocks and Gurux ``receiverReady()``
        requests are handled until Gurux reports that the response is
        complete.

        Args:
            reply: Optional existing ``GXReplyData`` instance. Supplying
                one allows segmented responses to accumulate in the same
                object.
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
                "Serial port is not open",
            )

        if self.hdlc is None:
            raise serial.SerialException(
                "HDLC reader is not initialized",
            )

        if reply is None:
            reply = GXReplyData()

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

            logger.debug(
                "Gurux requests more data: %s",
                reply.moreData,
            )

            ready = self.client.receiverReady(
                reply.moreData,
            )

            if ready is None:
                raise RuntimeError(
                    "Gurux did not generate Receiver Ready.",
                )

            if not isinstance(ready, list):
                ready = [ready]

            for request in ready:
                if request is None:
                    continue

                self.ser.write(request)
                self.ser.flush()
