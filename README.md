# home-energy-monitoring
Energy monitoring using 

- Fronius PV Inverter data
- Landys+Gyr E650 ZMD310 IEC 62056-21 Readout data
- Landys+Gyr E650 ZMD310 DLMS Readout data

## Architecture

```
    ┌──────────────────────────────────────────────┐
    │                 Raspberry Pi 4               │
    │                                              │
    │  Raspberry Pi OS 64-bit                      │
    │                                              │
    │  systemd                                     │
    │      │                                       │
    │      ▼                                       │
    │  Python collector                            │
    │      │                                       │
    │      ├──── Meter polling (5 sec)             │
    │      │                                       │
    │      ├──── PV HTTP polling                   │
    │      │                                       │
    │      ├──── Change detection                  │
    │      │                                       │
    │      ├──── Retry backoff                     │
    │      │                                       │
    │      └──── SQLite emergency queue            │
    │                    │                         │
    │                    ▼                         │
    │               InfluxDB                       │
    │                    │                         │
    │                    ▼                         │
    │                 Grafana                      │
    │                                              │
    └──────────────────────────────────────────────┘
                  │
           LAN / HTTPS
                  │
           ┌──────┴──────┐
           ▼             ▼
          PC          Android

```
    Component	    Recommendation	        Purpose
    Collector	    Python	                Poll meter + PV
    Database	    InfluxDB	            Store measurements
    Dashboard	    Grafana	                Graphs, current values, history
    Service         manager	systemd	        Automatic restart
    OS	            Raspberry Pi OS 64-bit	Host
    Storage	SSD     rather than SD card	    Reliability
    Reverse proxy	Optional Caddy/nginx	HTTPS/access control
    