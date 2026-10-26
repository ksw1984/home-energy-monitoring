"""Build BEMS battery adapters from configuration.

Both the HEMS app (shared measurement bus) and the standalone ``live_cli``
(database polling and dry runs) use this factory so vendor validation and
power limits cannot diverge between the two entry points.

The factory is synchronous: adapters open their network clients lazily and are
ready for concurrent ``asyncio`` calls immediately after construction.
"""

from src.bems.battery_adapter.anker import AnkerBatteryAdapter, AnkerModbusTcpClient
from src.bems.battery_adapter.marstek import MarstekBatteryAdapter, MarstekUdpClient
from src.bems.core.domain import Battery, BatteryProfile
from src.bems.services.live import MockBatteryAdapter
from src.config.config import BemsConfig, BemsDeviceConfig

AdapterClient = MarstekUdpClient | AnkerModbusTcpClient


def create_profile(config: BemsConfig, profile_id: str) -> BatteryProfile:
    """Convert a configured profile into the shared live battery model.

    Args:
        config: BEMS configuration containing the profile.
        profile_id: Profile key referenced by a device.

    Returns:
        Domain profile with capacity, SOC limits and efficiencies.
    """
    item = config.profiles[profile_id]
    return BatteryProfile(
        id=profile_id,
        capacity_wh=item.capacity_wh,
        min_soc_percent=item.min_soc_percent,
        max_soc_percent=item.max_soc_percent,
        max_charge_power_w=item.max_charge_power_w,
        max_discharge_power_w=item.max_discharge_power_w,
        charge_efficiency=item.charge_efficiency,
        discharge_efficiency=item.discharge_efficiency,
    )


def create_adapter(
    device: BemsDeviceConfig,
    config: BemsConfig,
    *,
    dry_run: bool,
) -> tuple[Battery, AdapterClient | None]:
    """Build a mock or explicitly enabled vendor adapter for one device.

    Args:
        device: Enabled live device configuration.
        config: BEMS configuration providing the profile and global limits.
        dry_run: Use an in-memory battery instead of a vendor client.

    Returns:
        The adapter and its shared network client, or ``None`` for mocks.

    Raises:
        RuntimeError: If a live device lacks a vendor, host, or explicit
            ``allow_control: true``.
    """
    profile = create_profile(config, device.profile)

    if dry_run:
        return (
            MockBatteryAdapter(profile=profile, initial_soc_percent=device.initial_soc_percent),
            None,
        )

    attributes = device.attributes
    vendor = attributes.get("vendor")

    if vendor not in {"marstek", "anker"}:
        raise RuntimeError(f"Live device '{device.id}' must set attributes.vendor to 'marstek' or 'anker'")

    if attributes.get("allow_control") is not True:
        raise RuntimeError(f"Live device '{device.id}' needs attributes.allow_control: true; hardware writes are disabled by default")

    host = attributes.get("host")

    if not isinstance(host, str) or not host:
        raise RuntimeError(f"Live device '{device.id}' requires attributes.host")

    if vendor == "anker":
        client = AnkerModbusTcpClient(
            host=host,
            port=int(attributes.get("port", 502)),
            unit_id=int(attributes.get("unit_id", 1)),
            timeout_s=float(attributes.get("timeout_s", 3.0)),
        )
        return (
            AnkerBatteryAdapter(
                client=client,
                capacity_wh=profile.capacity_wh,
                max_charge_power_w=min(profile.max_charge_power_w, device.max_output_power_w, config.control.max_output_power_w),
                max_discharge_power_w=min(profile.max_discharge_power_w, device.max_output_power_w, config.control.max_output_power_w),
                allow_control=True,
            ),
            client,
        )

    client = MarstekUdpClient(
        host=host,
        port=int(attributes.get("port", 30000)),
        timeout_s=float(attributes.get("timeout_s", 2.0)),
    )
    return (
        MarstekBatteryAdapter(
            client=client,
            capacity_wh=profile.capacity_wh,
            max_charge_power_w=min(profile.max_charge_power_w, device.max_output_power_w, config.control.max_output_power_w),
            max_discharge_power_w=min(profile.max_discharge_power_w, device.max_output_power_w, config.control.max_output_power_w),
            allow_control=True,
            passive_countdown_s=int(attributes.get("passive_countdown_s", 300)),
        ),
        client,
    )


def create_adapters(
    devices: list[BemsDeviceConfig],
    config: BemsConfig,
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Battery], list[AdapterClient]]:
    """Construct all adapters and return their shared clients for shutdown.

    Args:
        devices: Enabled live devices to instantiate.
        config: BEMS configuration providing profiles and limits.
        dry_run: Use in-memory batteries instead of vendor clients.

    Returns:
        Mapping of device ID to adapter plus every client that must be closed
        when the service stops.
    """
    batteries: dict[str, Battery] = {}
    clients: list[AdapterClient] = []

    for device in devices:
        adapter, client = create_adapter(device, config, dry_run=dry_run)
        batteries[device.id] = adapter

        if client is not None:
            clients.append(client)

    return batteries, clients
