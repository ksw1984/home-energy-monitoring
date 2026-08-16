from datetime import datetime, timezone
import requests


class FoniusSymoInverterCollector:
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

    def _now(self, timezone):
        return datetime.now(timezone)

    def collect(self):
        data = self._get_data()
        site = data["Body"]["Data"]["Site"]

        timestamp = datetime.fromisoformat(data["Head"]["Timestamp"])
        now = self._now(timestamp.tzinfo)

        return {
            "power_watt": site["P_PV"],
            "energy_day_wh": site["E_Day"],
            "energy_year_wh": site["E_Year"],
            "energy_total_wh": site["E_Total"],
            "timestamp": timestamp,
            "timestamp_age_seconds": (now - timestamp).total_seconds(),
        }

    def collect_current_power_watt(self):
        return self.collect()["power_watt"]
