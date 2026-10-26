"""CSV logging for battery setpoints sent by the live controller.

The log is deliberately independent of the measurement databases. It records
what the controller asked a battery to do and what the adapter reported back,
which makes a mock or pre-hardware run inspectable without querying InfluxDB.
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COMMAND_LOG_FIELDS = [
    "timestamp",
    "device_id",
    "requested_power_w",
    "accepted_power_w",
    "soc_percent",
    "status",
    "error",
]


class BatteryCommandLog:
    """Append one row per setpoint sent to a live battery adapter.

    The file is created with a header on first use. Rows are appended and
    flushed immediately so a crash does not lose commands that were already
    sent to hardware.
    """

    def __init__(self, path: str | Path) -> None:
        """Create a log at ``path`` and write the header when needed.

        Args:
            path: Destination CSV file. Parent directories are created.

        Raises:
            OSError: If the directory or file cannot be created.
        """
        self.path = Path(path)
        self._ensure_header()

    def record(  # noqa: PLR0913 - one keyword per CSV column.
        self,
        *,
        timestamp: datetime,
        device_id: str,
        requested_power_w: float,
        accepted_power_w: float | None,
        soc_percent: float | None,
        status: str,
        error: str = "",
    ) -> None:
        """Append one command row.

        Args:
            timestamp: Time of the control decision.
            device_id: Configured live device identifier.
            requested_power_w: Signed target sent to the adapter.
            accepted_power_w: Actual battery power read after the command, or
                ``None`` when the state could not be read.
            soc_percent: Battery SOC read after the command, or ``None``.
            status: ``accepted``, ``sent``, or ``failed``.
            error: Short failure description, empty for successful commands.
        """
        row = [
            timestamp.isoformat(),
            device_id,
            requested_power_w,
            "" if accepted_power_w is None else accepted_power_w,
            "" if soc_percent is None else soc_percent,
            status,
            error,
        ]

        try:
            with self.path.open("a", newline="", encoding="utf-8") as file:
                csv.writer(file).writerow(row)
        except OSError:
            # A logging failure must never stop or undo a battery command.
            logger.exception("Could not append to battery command log %s", self.path)

    def _ensure_header(self) -> None:
        """Create the parent directory and write the header if the file is new."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.path.exists() and self.path.stat().st_size > 0:
            return

        with self.path.open("w", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow(COMMAND_LOG_FIELDS)
