from datetime import date, datetime, timedelta
from typing import Any

import requests
from astral import Observer
from astral.sun import sun

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.fronius import FRONIUS_METRICS
from src.collectors.definitions.measurement import Measurement


class FroniusSymoInverterCollector(BaseCollector):
    """
    Collect PV power and daily energy values from a Fronius Symo inverter.

    The collector records ``pv_power`` on every successful collection.

    The Fronius energy counters ``E_Day``, ``E_Year`` and ``E_Total`` are
    recorded only once per day. A day is considered finished after sunset
    when the inverter has reported 0 W continuously for at least five minutes.

    Sunset is calculated from the configured PV installation latitude and
    longitude using Astral. This avoids relying on a fixed time such as
    15:00, which would not correctly represent the end of the solar day
    throughout the year.

    If the inverter cannot be reached, ``collect()`` returns no measurement.
    The separate ``get_current_power_watt()`` interface returns 0.0 when the
    inverter is unavailable, which is useful for power-control decisions.
    """

    SOURCE = "fronius"

    ZERO_POWER_DURATION = timedelta(minutes=5)

    def __init__(
        self,
        inverter_ip: str = "192.168.178.25",
        inverter_url: str = "solar_api/v1/GetPowerFlowRealtimeData.fcgi",
        pv_latitude: float = 52.4567,
        pv_longitude: float = 13.7213,
    ) -> None:
        """Initialize the Fronius Symo inverter collector.

        Args:
            inverter_ip: IP address of the Fronius inverter.
            inverter_url: API path used to retrieve the inverter's realtime
                power-flow data.
            pv_latitude: Latitude of the PV installation, used to calculate
                the local sunset time.
            pv_longitude: Longitude of the PV installation, used to calculate
                the local sunset time.
        """
        self.inverter_ip = inverter_ip
        self.inverter_url = f"http://{inverter_ip}/{inverter_url}"

        self.latitude = pv_latitude
        self.longitude = pv_longitude

        self._zero_power_since: datetime | None = None
        self._energy_recorded_for_date: date | None = None

    def _get_data(self) -> dict[str, Any]:
        """
        Fetch and validate the current data from the Fronius API.

        Returns:
            The decoded JSON response from the Fronius API.

        Raises:
            requests.exceptions.RequestException: If the HTTP request fails.
            RuntimeError: If the Fronius API reports an error.
        """
        response = requests.get(self.inverter_url, timeout=5)
        response.raise_for_status()

        data = response.json()

        if data["Head"]["Status"]["Code"] != 0:
            raise RuntimeError(f"Fronius API error: {data['Head']['Status']['Reason']}")

        return data

    def collect(self) -> list[Measurement]:
        """
        Collect the current PV power and, when appropriate, daily energy.

        ``pv_power`` is returned for every successful API request.

        The daily, yearly and total energy counters are returned only once
        per day. They are recorded after sunset once ``pv_power`` has remained
        at 0 W for at least five minutes.

        If the inverter is unavailable, no measurement is returned. This
        prevents an inverter communication failure from being interpreted as
        actual 0 W PV production.
        """
        try:
            data = self._get_data()
        except requests.exceptions.RequestException as exc:
            print(f"Fronius inverter unavailable: {exc}. " "No measurement recorded.")
            return []

        timestamp = datetime.fromisoformat(data["Head"]["Timestamp"])
        site = data["Body"]["Data"]["Site"]

        pv_power = float(site["P_PV"])

        measurements = [
            self._measurement(
                timestamp=timestamp,
                metric="pv_power",
                value=pv_power,
            )
        ]

        if self._should_finalize_day(timestamp, pv_power):
            measurements.extend(
                self._collect_energy_measurements(
                    timestamp=timestamp,
                    site=site,
                )
            )

            self._energy_recorded_for_date = timestamp.date()
            self._zero_power_since = None

        return measurements

    def get_current_power_watt(self) -> float:
        """
        Return current PV power for power-control purposes.

        Returns:
            Current PV power in watts, or ``0.0`` if the inverter is
            unavailable or the Fronius API reports an error.
        """
        try:
            data = self._get_data()
        except (requests.exceptions.RequestException, RuntimeError):
            return 0.0

        return float(data["Body"]["Data"]["Site"]["P_PV"])

    def _should_finalize_day(
        self,
        timestamp: datetime,
        pv_power: float,
    ) -> bool:
        """
        Return whether the current measurement completes the solar day.

        A day can only be finalized after sunset. After sunset, the inverter
        must continuously report 0 W for at least ``ZERO_POWER_DURATION``.

        Any non-zero PV power resets the zero-power period.

        Args:
            timestamp: Timestamp of the current inverter measurement.
            pv_power: Current PV power in watts.

        Returns:
            ``True`` if the daily energy values should be recorded now,
            otherwise ``False``.
        """
        # it is not after sunset
        if not self._is_after_sunset(timestamp):
            self._zero_power_since = None
            return False

        # already saved daily values
        if self._energy_recorded_for_date == timestamp.date():
            return False

        # PV is producing still
        if pv_power > 0:
            self._zero_power_since = None
            return False

        # first 0 W measurement
        if self._zero_power_since is None:
            self._zero_power_since = timestamp
            return False

        # 0 W since at least 5 mins
        return timestamp - self._zero_power_since >= self.ZERO_POWER_DURATION

    def _is_after_sunset(self, timestamp: datetime) -> bool:
        """
        Return whether the given timestamp is at or after local sunset.

        Sunset is calculated for the configured PV installation coordinates
        and the timezone of the supplied timestamp.

        Args:
            timestamp: Timestamp to evaluate.

        Returns:
            ``True`` if the timestamp is at or after sunset.
        """
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")

        observer = Observer(
            latitude=self.latitude,
            longitude=self.longitude,
        )

        sunset = sun(
            observer,
            date=timestamp.date(),
            tzinfo=timestamp.tzinfo,
        )["sunset"]

        return timestamp >= sunset

    def _collect_energy_measurements(
        self,
        timestamp: datetime,
        site: dict[str, Any],
    ) -> list[Measurement]:
        """
        Create measurements for the Fronius energy counters.

        Args:
            timestamp: Timestamp to assign to the measurements.
            site: Fronius ``Site`` response data.

        Returns:
            Measurements for daily, yearly and total PV energy.
        """

        return [
            self._measurement(
                timestamp=timestamp,
                metric="pv_energy_day",
                value=site["E_Day"],
            ),
            self._measurement(
                timestamp=timestamp,
                metric="pv_energy_year",
                value=site["E_Year"],
            ),
            self._measurement(
                timestamp=timestamp,
                metric="pv_energy_total",
                value=site["E_Total"],
            ),
        ]

    def _measurement(
        self,
        timestamp: datetime,
        metric: str,
        value: float,
    ) -> Measurement:
        """
        Create a Measurement using the configured Fronius metric definition.

        Args:
            timestamp: Timestamp of the measurement.
            metric: Fronius metric name.
            value: Numeric measurement value.

        Returns:
            A populated Measurement instance.

        Raises:
            RuntimeError: If no definition exists for the requested metric.
        """
        try:
            definition = FRONIUS_METRICS[metric]
        except KeyError as exc:
            raise RuntimeError(f"No Fronius metric definition for '{metric}'") from exc

        return Measurement(
            timestamp=timestamp,
            source=self.SOURCE,
            metric=metric,
            value=float(value),
            unit=definition["unit"],
        )
