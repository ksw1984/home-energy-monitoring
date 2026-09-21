FRONIUS_METRICS = {
    # -------------------------------------------------------------------------
    # REST API
    # -------------------------------------------------------------------------
    # Current PV power on the AC side of the inverter.
    "pv_power": {
        "description_en": "Current PV power (AC)",
        "description_de": "Aktuelle PV-Leistung (AC)",
        "unit": "W",
        "category": "power",
    },
    "pv_energy_day": {
        "description_en": "PV energy today (AC)",
        "description_de": "PV-Erzeugung heute (AC)",
        "unit": "Wh",
        "category": "energy",
    },
    "pv_energy_year": {
        "description_en": "PV energy this year (AC)",
        "description_de": "PV-Erzeugung dieses Jahr (AC)",
        "unit": "Wh",
        "category": "energy",
    },
    "pv_energy_total": {
        "description_en": "Total PV energy (AC)",
        "description_de": "Gesamte PV-Erzeugung (AC)",
        "unit": "Wh",
        "category": "energy",
    },
    # -------------------------------------------------------------------------
    # Modbus / SunSpec
    # -------------------------------------------------------------------------
    # Total inverter power and energy, AC side.
    "ac_power": {
        "description_en": "Current inverter power (AC)",
        "description_de": "Aktuelle Wechselrichterleistung (AC)",
        "unit": "W",
        "category": "power",
    },
    "ac_energy_total": {
        "description_en": "Total inverter energy (AC)",
        "description_de": "Gesamte Wechselrichter-Energie (AC)",
        "unit": "Wh",
        "category": "energy",
    },
    # MPPT 1, DC side.
    "mppt_1_power": {
        "description_en": "MPPT 1 power (DC)",
        "description_de": "Leistung MPPT 1 (DC)",
        "unit": "W",
        "category": "power",
    },
    "mppt_1_energy_total": {
        "description_en": "MPPT 1 total energy (DC)",
        "description_de": "Gesamte Energie MPPT 1 (DC)",
        "unit": "Wh",
        "category": "energy",
    },
    # MPPT 2, DC side.
    "mppt_2_power": {
        "description_en": "MPPT 2 power (DC)",
        "description_de": "Leistung MPPT 2 (DC)",
        "unit": "W",
        "category": "power",
    },
    "mppt_2_energy_total": {
        "description_en": "MPPT 2 total energy (DC)",
        "description_de": "Gesamte Energie MPPT 2 (DC)",
        "unit": "Wh",
        "category": "energy",
    },
}
