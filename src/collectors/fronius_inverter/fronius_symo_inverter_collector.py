from datetime import datetime
import requests

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.collectors.definitions.fronius import FRONIUS_METRICS


class FoniusSymoInverterCollector(BaseCollector):
    SOURCE = "fronius"

    def __init__(
        self,
        inverter_ip="192.168.178.25",
        inverter_url="solar_api/v1/GetPowerFlowRealtimeData.fcgi",
    ):
        self.inverter_ip = inverter_ip
        self.inverter_url = f"http://{inverter_ip}/{inverter_url}"

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
        data = self._get_data()

        site = data["Body"]["Data"]["Site"]

        timestamp = datetime.fromisoformat(data["Head"]["Timestamp"])

        return [
            self._measurement(
                timestamp=timestamp,
                metric="pv_power",
                value=site["P_PV"],
            ),
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

    def collect_current_power_watt(self) -> float:
        """
        Return the current PV power in watts.
        """
        measurements = self.collect()

        for measurement in measurements:
            if measurement.metric == "pv_power":
                return measurement.value

        raise RuntimeError("Fronius response did not contain pv_power")
