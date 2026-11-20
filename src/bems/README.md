# Battery Energy Management System (BEMS)

The BEMS controls and evaluates AC-coupled batteries independently of the HEMS polling loop. It supports historical simulations, live control with an in-memory battery, and live control through Anker or Marstek adapters.

## What it does

- Reads grid, household, and PV power from the configured database for simulations and the standalone CLI.
- Reads live power directly from the in-process measurement bus when hosted inside HEMS (`feed: bus`).
- Uses import-positive power internally; HEMS meter values already use this sign convention and are converted to watts at the BEMS boundary.
- Charges only from measured grid export (PV surplus).
- Serves household demand first. Heat-pump demand is served only from energy above the calculated overnight reserve.
- Limits power by battery profile, per-device setting, and the global BEMS limit.
- Models charge/discharge efficiency, power ramping, SOC limits, and energy losses in historical simulations.
- Stores requested power separately from actual power and state measurements.
- Requires an available device and an explicitly approved model/firmware before live hardware writes.
- Requests 0 W when live inputs become stale, which deactivates both battery charging and feed-in. This safety stop does not depend on reading the device identity or firmware first.

The night reserve uses a configured baseline household load, astronomical dusk/dawn, and configurable margins. The default baseline is 250 W, with 30 minutes of margin on each side. Forecast support is an interface only; its current provider returns 0 kWh.

## Configure devices

Edit the `bems` section in the repository-root `config.yaml`:

1. Set `history_database` to an enabled database that supports measurement reads.
2. Check the `inputs` source and metric names against the HEMS measurements.
3. Set tariffs, control limits, night baseline/location, and each battery profile.
4. Keep simulated and live devices as separate entries. Their `profile`, `initial_soc_percent`, and `max_output_power_w` are configured independently.

The sample configuration includes `anker_sim` and `marstek_sim` (enabled) and corresponding live entries (disabled). The sample starts each simulated battery at 50% SOC and limits output to 800 W. Device output is always capped by profile and global limits (3.5 kW Anker, 2.5 kW Marstek in the sample profiles).

## Historical simulation

Historical simulations run on demand in their own process; they do not add work to HEMS monitoring.

1. Ensure grid and household measurements exist for the requested time range and the configured history database can read them.
2. Enable the desired `mode: simulation` devices in `config.yaml`.
3. Run from the repository root:

   ```bash
   poetry run bems-simulate \
     --from "2026-06-01T00:00:00+02:00" \
     --to "2026-06-08T00:00:00+02:00" \
     --devices anker_sim,marstek_sim
   ```

   `--from` is inclusive and `--to` exclusive. Omit `--devices` to run all enabled simulation devices. Every run gets a generated ID unless `--run-id` is supplied.
4. Open `data/bems-reports/<run-id>/index.html`. The report includes daily and weekly summaries, daily saved grid-import kWh, cost savings, and plots of measured grid/household/heat-pump/PV values alongside simulated grid power, battery power, and SOC.
5. Use `timeseries_<device-id>.csv` for detailed local results and `measurements_<device-id>.jsonl` for the raw local simulation output. Daily and weekly summary CSVs are generated beside the HTML report.

The simulation only reads input measurements from the configured history database. It does not write simulated measurements to any database. Each device is simulated independently against the same meter history. A new run starts at the configured initial SOC. `--to` defaults to the current time; `--from` is required.

### Run the simulation from another computer on the LAN

The simulation process runs on the computer where `bems-simulate` is started. Its local reports are written on that computer.

1. Check out the repository on the development computer and install project dependencies.
2. BEMS gets the database type, URL, organization, and bucket from `databases` in `config.yaml`; `bems.history_database` selects the configured input database. Provide the InfluxDB token in the development computer's environment. Do not commit the token.
3. Make the configured endpoint reachable from the development computer. The example hostname `influxdb` usually resolves only inside the Docker network. If it resolves on the development computer, no URL override is needed.
4. Otherwise, override the configured URL with the HEMS host's LAN address and run the simulation, for example in PowerShell:

   ```powershell
   $env:INFLUXDB_URL = "http://192.168.178.20:8086"
   poetry run bems-simulate --from "2026-06-01T00:00:00+02:00" --devices anker_sim,marstek_sim
   ```

   `INFLUXDB_URL` is optional and only overrides `databases[].attributes.url`. Replace the sample address with the HEMS host's actual LAN address. InfluxDB must publish port 8086 to the LAN and allow the development computer through its firewall.

## Live controller with simulated batteries

This runs the live controller loop against current meter measurements while replacing the battery adapters with in-memory mock batteries. It is useful to observe requested power and controller measurements without sending commands to hardware.

1. Temporarily enable a `mode: live` device in `config.yaml`. Its adapter attributes are ignored by `--mock`.
2. Ensure the configured input database is enabled and receiving fresh grid and household measurements.
3. Run one safe cycle:

   ```bash
   poetry run bems-live --mock --once
   ```

4. Run continuously by omitting `--once`:

   ```bash
   poetry run bems-live --mock
   ```

The mock starts at the device's configured `initial_soc_percent`; its state is in memory and resets when the process restarts. Every accepted setpoint is appended to `data/bems_mock_commands.csv` by default. Use `--mock-csv <path>` to choose another file. The CSV contains `timestamp`, `device_id`, `requested_power_w`, `accepted_power_w`, and `soc_percent`, and is written on exit (including Ctrl-C), so it can be plotted without querying InfluxDB. Live-loop measurements use source `bems_live_<device-id>` and include requested/actual power, SOC, energy, input freshness, device availability, and firmware/control status. This mode does not calculate historical cost savings; use `bems-simulate` for that comparison.

## App-hosted mock and command log

The HEMS app can host the live controller against a mock battery. Set `attributes.mock: true` on an enabled `mode: live` device; no vendor, host, or `allow_control` setting is required.

```yaml
- id: anker_mock
  profile: anker_solarbank_max_ac
  mode: live
  enabled: true
  initial_soc_percent: 50
  max_output_power_w: 800
  attributes:
    mock: true
```

Keep the matching real device disabled while the mock is enabled. The app substitutes the synthetic `BEMS-MOCK` / `mock` identity for mock devices, so the real firmware allowlist is not needed. Mixed configurations are supported: real devices still go through their normal identity and firmware checks.

Set `bems.control.command_log_csv` to append every setpoint actually sent to an adapter:

```yaml
bems:
  control:
    command_log_csv: data/bems_commands.csv
```

The CSV columns are `timestamp`, `device_id`, `requested_power_w`, `accepted_power_w`, `soc_percent`, `status`, and `error`. `status` is `accepted` when the adapter state was read after the write, `sent` when the write succeeded but state could not be read, and `failed` when the write itself failed. The live service appends and flushes each row, so commands survive a crash. This log works for both mock and real adapters. Without hardware, an Anker adapter fails at identity/state preflight before `set_power`, so no command row is produced; use `attributes.mock: true` with the Anker profile to exercise the control path before the battery arrives.

The standalone `bems-live` path also honors `bems.control.command_log_csv`. Its `--mock-csv` export remains available as a separate one-file plotting snapshot.

## Live feed and stale-input safety

`bems.control.feed` selects where live control gets its measurements:

- `bus` (default): HEMS publishes each collector batch to an in-process bus before writing to the database. The app-hosted BEMS uses that stream, so control does not wait for database storage. With `storage_interval: 5` the database sees every fifth sample while live control still sees every sample. Start HEMS normally.
- `database`: the standalone `bems-live` CLI polls the configured history database. This path is intended for dry runs and diagnostics.

Two different timeouts exist:

- `max_measurement_age_s` is simulation/replay only. Historical runs use it to detect input gaps before integrating energy.
- `live_max_measurement_age_s` (default 3 s) is live only. If no fresh grid sample arrives within that window, the controller requests 0 W from every live battery. A 0 W target deactivates both charging and feed-in; the command is attempted even when identity/firmware preflight is unavailable.

When `feed: bus`, `bems-live` refuses real hardware control to avoid running two controllers for the same battery. Use the HEMS app for live hardware control, or temporarily set `feed: database` for a standalone live run.

## Live controller with a real battery

Live hardware adapters exist for the Marstek Venus E 3.0 and Anker SOLIX Solarbank Max AC. Hardware writes are opt-in and compatibility-gated.

1. Configure the live device's profile, measured device IP, vendor, and transport settings. Keep `enabled: false` until setup and identity checks are complete.
2. Read the device's exact reported model and firmware using the vendor adapter's read-only checks or a mock/dry setup. Set `expected_model` and `supported_firmware_versions` to explicit verified values; the firmware list must contain the exact version token.
3. Verify the correct network protocol and enable the device's local control feature where required. Anker uses Modbus TCP; Marstek uses its local UDP API.
4. Set `attributes.allow_control: true`, then `enabled: true` only when you intend to permit hardware writes. Missing identity allowlists, mismatched model/firmware, communication failure, or stale meter readings block or stop power requests.
5. Start the controller:

   - With the HEMS app (default `feed: bus`), start HEMS; it hosts the live controller as a sibling task.
   - Standalone, set `feed: database` first, then run:

   ```bash
   poetry run bems-live
   ```

6. Monitor `bems_live_<device-id>` for `requested_power`, `actual_power`, `soc`, `device_alive`, `firmware_compatible`, `control_ok`, and `stale_input`.

The live service is a separate process from HEMS monitoring only in `feed: database` mode. Do not enable more than one live controller for the same physical battery.

## Measurement names

| Source | Example metrics | Purpose |
| --- | --- | --- |
| `bems_sim_<device-id>_<run-id>` | `requested_power`, `actual_power`, `soc`, `energy_stored`, `charge_loss_total`, `discharge_loss_total`, `savings_total` | Source identifier inside local simulation JSONL output |
| `bems_live_<device-id>` | `requested_power`, `actual_power`, `soc`, `energy_stored`, `device_alive`, `firmware_compatible`, `control_ok`, `stale_input` | Live controller/device status |

## Tests and checks

```bash
poetry run pytest test/bems -q
poetry run ruff check .
poetry run pyrefly check
poetry run ruff format .
```
