from dataclasses import dataclass


@dataclass(frozen=True)
class ObisDefinition:
    code: str
    metric: str
    description_en: str
    description_de: str
    unit: str
    category: str
    dlms_logical_name: str | None = None  # for dlms
    dlms_short_name: int | None = None  # for dlms
    dlms_unit_multiplier: float = 1.0


def get_obis_definition(obis: str) -> ObisDefinition | None:
    """
    Return the definition for an OBIS code.

    Historical OBIS values such as 1.8.0*62 are resolved
    against their base OBIS code 1.8.0.
    """
    base_obis = obis.split("*", 1)[0]

    return OBIS_DEFINITIONS.get(base_obis)


# Current values we want to store.
CURRENT_OBIS = {
    "1.5.0",  # Aktuelle Wirkleistung Bezug
    "2.5.0",  # Aktuelle Wirkleistung Einspeisung
    # "1.6.0",  # Max Wirkleistung Bezug
    # "2.6.0",  # Max Wirkleistung Einspeisung
    "1.8.0",  # Zählerstand Gesamtbezug
    "2.8.0",  # Zählerstand Gesamteinspeisung
    # "5.8.0",  # Blindenergie
    # "32.7.0",  # Voltages Phasen
    # "52.7.0",
    # "72.7.0",
    # "31.7.0",  # Currents Phasen
    # "51.7.0",
    # "71.7.0",
    "16.7.0",  # Gesamt-Wirkleistung
    # "36.7.0",  # Wirkleistung Phasen
    # "56.7.0",
    # "76.7.0",
    # "131.7.0",  # "Gesamt-Blindleistung"
    # "151.7.0",  # Blindleistungen Phasen
    # "171.7.0",
    # "191.7.0",
}

OBIS_DEFINITIONS = {
    #
    # Meter data
    #
    "0.0.0": ObisDefinition(
        code="0.0.0",
        metric="meter_serial_number",
        description_en="Meter serial number",
        description_de="Zähler-/Seriennummer",
        unit="",
        category="device",
        dlms_logical_name="0.0.42.0.0.255",
        dlms_short_name=0xFD00,
    ),
    "0.1.0": ObisDefinition(
        code="0.1.0",
        metric="profile_configuration",
        description_en="Profile configuration",
        description_de="Profil-/Periodenkonfiguration",
        unit="",
        category="device",
    ),
    "0.1.2": ObisDefinition(
        code="0.1.2",
        metric="profile_period_timestamp",
        description_en="Profile period timestamp",
        description_de="Zeitstempel der Profilperiode",
        unit="",
        category="timestamp",
    ),
    "0.2.0": ObisDefinition(
        code="0.2.0",
        metric="firmware_version",
        description_en="Firmware/program version",
        description_de="Firmware-/Programmierstand",
        unit="",
        category="device",
        dlms_logical_name="1.0.0.2.0.255",
        dlms_short_name=0xFF00,
    ),
    "0.2.1": ObisDefinition(
        code="0.2.1",
        metric="software_identifier",
        description_en="Firmware/software identifier",
        description_de="Firmware-/Softwarekennung",
        unit="",
        category="device",
        dlms_logical_name="1.0.0.2.1.255",
        dlms_short_name=0x39D0,
    ),
    "0.2.8": ObisDefinition(
        code="0.2.8",
        metric="meter_status",
        description_en="Status/version",
        description_de="Status/Version",
        unit="",
        category="device",
        dlms_logical_name="1.0.0.2.8.255",
        dlms_short_name=0x0498,
    ),
    "0.9.1": ObisDefinition(
        code="0.9.1",
        metric="meter_time",
        description_en="Meter time",
        description_de="Zählerzeit",
        unit="",
        category="device",
    ),
    "0.9.2": ObisDefinition(
        code="0.9.2",
        metric="meter_date",
        description_en="Meter date",
        description_de="Zählerdatum",
        unit="",
        category="device",
    ),
    #
    # Current active power / Aktuelle Wirkleistung
    #
    "1.5.0": ObisDefinition(
        code="1.5.0",
        metric="grid_import_power",
        description_en="Current active import power",
        description_de="Aktuelle Wirkleistung Bezug",
        unit="kW",
        category="power",
    ),
    "2.5.0": ObisDefinition(
        code="2.5.0",
        metric="grid_export_power",
        description_en="Current active export power",
        description_de="Aktuelle Wirkleistung Einspeisung",
        unit="kW",
        category="power",
    ),
    #
    # Historical maximum active power / Historische Maximalleistung
    #
    # These are currently only definitions.
    # The collectors does not collect them yet.
    #
    "1.6.0": ObisDefinition(
        code="1.6.0",
        metric="grid_import_power_max",
        description_en="Maximum active import power",
        description_de="Maximale Wirkleistung Bezug",
        unit="kW",
        category="power_max",
        dlms_logical_name="1.1.1.6.0.255",
        dlms_short_name=0x3E18,
        dlms_unit_multiplier=0.001,
    ),
    "2.6.0": ObisDefinition(
        code="2.6.0",
        metric="grid_export_power_max",
        description_en="Maximum active export power",
        description_de="Maximale Wirkleistung Einspeisung",
        unit="kW",
        category="power_max",
        dlms_logical_name="1.1.2.6.0.255",
        dlms_short_name=0x3E90,
        dlms_unit_multiplier=0.001,
    ),
    #
    # Total energy / Zählerstand
    #
    "1.8.0": ObisDefinition(
        code="1.8.0",
        metric="grid_import_energy_total",
        description_en="Total grid import energy",
        description_de="Gesamtbezug",
        unit="kWh",
        category="energy",
        dlms_logical_name="1.1.1.8.0.255",
        dlms_short_name=0x1CE0,
        dlms_unit_multiplier=0.001,
    ),
    "2.8.0": ObisDefinition(
        code="2.8.0",
        metric="grid_export_energy_total",
        description_en="Total grid export energy",
        description_de="Gesamteinspeisung",
        unit="kWh",
        category="energy",
        dlms_logical_name="1.1.2.8.0.255",
        dlms_short_name=0x1D98,
        dlms_unit_multiplier=0.001,
    ),
    #
    # Reactive energy / Blindenergie
    #
    "5.8.0": ObisDefinition(
        code="5.8.0",
        metric="reactive_energy_5",
        description_en="Reactive energy",
        description_de="Blindenergie",
        unit="kvarh",
        category="energy",
        dlms_logical_name="1.1.5.8.0.255",
        dlms_short_name=0x2478,
        dlms_unit_multiplier=0.001,
    ),
    "6.8.0": ObisDefinition(
        code="6.8.0",
        metric="reactive_energy_6",
        description_en="Additional reactive energy",
        description_de="Weitere Blindenergie",
        unit="kvarh",
        category="energy",
        dlms_logical_name="1.1.6.8.0.255",
        dlms_short_name=0x2528,
        dlms_unit_multiplier=0.001,
    ),
    "7.8.0": ObisDefinition(
        code="7.8.0",
        metric="reactive_energy_7",
        description_en="Additional reactive energy",
        description_de="Weitere Blindenergie",
        unit="kvarh",
        category="energy",
        dlms_logical_name="1.1.7.8.0.255",
        dlms_short_name=0x25D8,
        dlms_unit_multiplier=0.001,
    ),
    "8.8.0": ObisDefinition(
        code="8.8.0",
        metric="reactive_energy_8",
        description_en="Additional reactive energy",
        description_de="Weitere Blindenergie",
        unit="kvarh",
        category="energy",
        dlms_logical_name="1.1.8.8.0.255",
        dlms_short_name=0x2688,
        dlms_unit_multiplier=0.001,
    ),
    #
    # Voltages / Spannungen
    #
    "32.7.0": ObisDefinition(
        code="32.7.0",
        metric="voltage_l1",
        description_en="L1 voltage",
        description_de="Spannung L1",
        unit="V",
        category="voltage",
        dlms_logical_name="1.1.32.7.0.255",
        dlms_short_name=0x9AE8,
        dlms_unit_multiplier=1,
    ),
    "52.7.0": ObisDefinition(
        code="52.7.0",
        metric="voltage_l2",
        description_en="L2 voltage",
        description_de="Spannung L2",
        unit="V",
        category="voltage",
        dlms_logical_name="1.1.52.7.0.255",
        dlms_short_name=0x9B78,
        dlms_unit_multiplier=1,
    ),
    "72.7.0": ObisDefinition(
        code="72.7.0",
        metric="voltage_l3",
        description_en="L3 voltage",
        description_de="Spannung L3",
        unit="V",
        category="voltage",
        dlms_logical_name="1.1.72.7.0.255",
        dlms_short_name=0x9C08,
        dlms_unit_multiplier=1,
    ),
    #
    # Currents / Ströme
    #
    "31.7.0": ObisDefinition(
        code="31.7.0",
        metric="current_l1",
        description_en="L1 current",
        description_de="Strom L1",
        unit="A",
        category="current",
        dlms_logical_name="1.1.31.7.0.255",
        dlms_short_name=0x9C98,
        dlms_unit_multiplier=1,
    ),
    "51.7.0": ObisDefinition(
        code="51.7.0",
        metric="current_l2",
        description_en="L2 current",
        description_de="Strom L2",
        unit="A",
        category="current",
        dlms_logical_name="1.1.51.7.0.255",
        dlms_short_name=0x9D28,
        dlms_unit_multiplier=1,
    ),
    "71.7.0": ObisDefinition(
        code="71.7.0",
        metric="current_l3",
        description_en="L3 current",
        description_de="Strom L3",
        unit="A",
        category="current",
        dlms_logical_name="1.1.71.7.0.255",
        dlms_short_name=0x9DB8,
        dlms_unit_multiplier=1,
    ),
    #
    # Active power / Wirkleistung
    #
    "16.7.0": ObisDefinition(
        code="16.7.0",
        metric="grid_active_power",
        description_en="Total active power",
        description_de="Gesamt-Wirkleistung",
        unit="kW",
        category="power",
        dlms_logical_name="1.1.16.7.0.255",
        dlms_short_name=0x9F68,
        dlms_unit_multiplier=0.001,
    ),
    "36.7.0": ObisDefinition(
        code="36.7.0",
        metric="active_power_l1",
        description_en="L1 active power",
        description_de="Wirkleistung L1",
        unit="kW",
        category="power",
        dlms_logical_name="1.1.36.7.0.255",
        dlms_short_name=0xBA68,
        dlms_unit_multiplier=0.001,
    ),
    "56.7.0": ObisDefinition(
        code="56.7.0",
        metric="active_power_l2",
        description_en="L2 active power",
        description_de="Wirkleistung L2",
        unit="kW",
        category="power",
        dlms_logical_name="1.1.56.7.0.255",
        dlms_short_name=0xBAF8,
        dlms_unit_multiplier=0.001,
    ),
    "76.7.0": ObisDefinition(
        code="76.7.0",
        metric="active_power_l3",
        description_en="L3 active power",
        description_de="Wirkleistung L3",
        unit="kW",
        category="power",
        dlms_logical_name="1.1.76.7.0.255",
        dlms_short_name=0xBB88,
        dlms_unit_multiplier=0.001,
    ),
    #
    # Reactive power / Blindleistung
    #
    "131.7.0": ObisDefinition(
        code="131.7.0",
        metric="reactive_power_total",
        description_en="Total reactive power",
        description_de="Gesamt-Blindleistung",
        unit="kvar",
        category="reactive_power",
    ),
    "151.7.0": ObisDefinition(
        code="151.7.0",
        metric="reactive_power_l1",
        description_en="L1 reactive power",
        description_de="Blindleistung L1",
        unit="kvar",
        category="reactive_power",
    ),
    "171.7.0": ObisDefinition(
        code="171.7.0",
        metric="reactive_power_l2",
        description_en="L2 reactive power",
        description_de="Blindleistung L2",
        unit="kvar",
        category="reactive_power",
    ),
    "191.7.0": ObisDefinition(
        code="191.7.0",
        metric="reactive_power_l3",
        description_en="L3 reactive power",
        description_de="Blindleistung L3",
        unit="kvar",
        category="reactive_power",
    ),
}
