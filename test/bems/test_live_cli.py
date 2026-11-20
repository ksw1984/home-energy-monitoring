import asyncio
import csv
from datetime import datetime, UTC

from src.bems.core.domain import BatteryPowerCommand, BatteryProfile
from src.bems.live_cli import _write_mock_commands
from src.bems.services.live import MockBatteryAdapter


def _profile() -> BatteryProfile:
    """Return a small profile that makes command clamping observable."""
    return BatteryProfile(
        id="test",
        capacity_wh=1000.0,
        min_soc_percent=10.0,
        max_soc_percent=90.0,
        max_charge_power_w=500.0,
        max_discharge_power_w=500.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
    )


def test_write_mock_commands_csv(tmp_path):
    adapter = MockBatteryAdapter(profile=_profile(), initial_soc_percent=50.0)
    asyncio.run(
        adapter.set_power(
            BatteryPowerCommand(
                timestamp=datetime(2026, 10, 26, 12, tzinfo=UTC),
                power_w=-600.0,
            )
        )
    )
    path = tmp_path / "commands.csv"

    _write_mock_commands(path, {"test_live": adapter})

    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    assert len(rows) == 1
    assert rows[0]["device_id"] == "test_live"
    assert float(rows[0]["requested_power_w"]) == -600.0
    assert float(rows[0]["accepted_power_w"]) == -500.0
    assert float(rows[0]["soc_percent"]) == 50.0


def test_write_mock_commands_writes_header_for_idle_mock(tmp_path):
    adapter = MockBatteryAdapter(profile=_profile(), initial_soc_percent=50.0)
    path = tmp_path / "commands.csv"

    _write_mock_commands(path, {"test_live": adapter})

    assert path.read_text(encoding="utf-8").splitlines() == ["timestamp,device_id,requested_power_w,accepted_power_w,soc_percent"]
