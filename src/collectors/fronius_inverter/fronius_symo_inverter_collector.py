from datetime import datetime, timedelta

import requests
from astral import Observer
from astral.sun import sun


from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.collectors.definitions.fronius import FRONIUS_METRICS


class FroniusSymoInverterCollector(BaseCollector):
    SOURCE = "fronius"

    ZERO_POWER_DURATION = timedelta(minutes=5)

    def __init__(
        self,
        inverter_ip="192.168.178.25",
        inverter_url="solar_api/v1/GetPowerFlowRealtimeData.fcgi",
        pv_latitude=52.4567,
        pv_longitude=13.7213,
    ):
        self.inverter_ip = inverter_ip
        self.inverter_url = f"http://{inverter_ip}/{inverter_url}"

        self.latitude = pv_latitude
        self.longitude = pv_longitude

        self._zero_power_since = None
        self._energy_recorded_for_date = None

    def _get_data(self):
        """
        {
            "Body": {
                "Data": {
                    "Inverters": {
                        "1": {
                            "DT": 114,
                            "E_Day": 32880,
                            "E_Total": 90416904,
                            "E_Year": 13947576,
                            "P": 10924
                        }
                    },
                    "Site": {
                        "E_Day": 32880,
                        "E_Total": 90416904,
                        "E_Year": 13947576,
                        "Meter_Location": "unknown",
                        "Mode": "produce-only",
                        "P_Akku": null,
                        "P_Grid": null,
                        "P_Load": null,
                        "P_PV": 10924,
                        "rel_Autonomy": null,
                        "rel_SelfConsumption": null
                    },
                    "Version": "12"
                }
            },
            "Head": {
                "RequestArguments": {},
                "Status": {
                    "Code": 0,
                    "Reason": "",
                    "UserMessage": ""
                },
                "Timestamp": "2026-08-15T11:43:28+02:00"
            }
        }
        """
        response = requests.get(self.inverter_url, timeout=5)
        response.raise_for_status()

        data = response.json()

        if data["Head"]["Status"]["Code"] != 0:
            raise RuntimeError(f"Fronius API error: {data['Head']['Status']['Reason']}")

        return data

    def collect(self) -> list[Measurement]:
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
        """Return current PV power for control purposes.

        Returns 0.0 if the inverter is unavailable or the value
        cannot be retrieved.
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

        # Noch nicht nach Sonnenuntergang
        if not self._is_after_sunset(timestamp):
            self._zero_power_since = None
            return False

        # Tageswerte für diesen Tag bereits gespeichert
        if self._energy_recorded_for_date == timestamp.date():
            return False

        # PV produziert noch
        if pv_power > 0:
            self._zero_power_since = None
            return False

        # Erste 0-W-Messung
        if self._zero_power_since is None:
            self._zero_power_since = timestamp
            return False

        # Seit mindestens 5 Minuten 0 W
        return timestamp - self._zero_power_since >= self.ZERO_POWER_DURATION

    def _is_after_sunset(self, timestamp: datetime) -> bool:
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
        site: dict,
    ) -> list[Measurement]:

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
