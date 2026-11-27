# Battery Energy Management System (BEMS)

The BEMS controls and evaluates AC-coupled batteries independently of the HEMS polling loop. It supports historical simulations, live control with an in-memory battery, and live control through Anker or Marstek adapters.

## What it does

- Reads grid, household, and PV power from the configured database for simulations and the standalone CLI.
- Reads live grid and household meter values from the standalone MQTT broker; live control does not start inside HEMS.
- Uses import-positive power internally; HEMS meter values already use this sign convention and are converted to watts at the BEMS boundary.
- Charges only from measured grid export (PV surplus).
- Serves household demand first. Heat-pump demand is served only from energy above the calculated household reserve until the next night-reserve end (including daytime), so the battery can be charged up to that reserve before heat-pump discharge uses surplus energy.
- Limits power by battery profile, per-device setting, and the global BEMS limit.
- Models charge/discharge efficiency, power ramping, SOC limits, and energy losses in historical simulations.
- Stores requested power separately from actual power and state measurements.
- Requires an available device and an explicitly approved model/firmware before live hardware writes.
- Requests 0 W when live inputs become stale, which deactivates both battery charging and feed-in. This safety stop does not depend on reading the device identity or firmware first.

The night reserve uses a configurable household baseline (300 W fallback), astronomical dusk and sunrise with configurable margins (dusk −30 min and sunrise +50 min in the sample config), and an optional measured baseline derived from completed nights. The reserve is applied day and night until the next dawn+margin boundary; it protects expected household baseline energy, while energy above it remains available for heat-pump discharge when the sustain gate allows it. Forecast support is an interface only; its current provider returns 0 kWh.

## Configure devices

Edit the `bems` section in the repository-root `config.yaml`:

1. Set `bems.history_database` to an enabled database that supports measurement reads.
2. Check the `inputs` source and metric names against the HEMS measurements.
3. Set tariffs, control limits, night baseline/location, and each battery profile.
4. Keep simulated and live devices as separate entries. Their `profile`, `initial_soc_percent`, and `max_output_power_w` are configured independently.

The sample configuration includes `anker_sim` and `marstek_sim` (enabled) and corresponding live entries (disabled). The sample starts each simulated battery at 50% SOC and limits output to 800 W. Device output is always capped by profile and global limits (3.5 kW Anker, 2.5 kW Marstek in the sample profiles).

## Measured night baseline

Set `bems.night.baseline_estimation.enabled: true` to replace the fixed `baseline_household_w` with a measured night baseline. Each completed night is stored as one logical `bems_night_household` record with `night_energy_kwh`, `night_duration_h`, `night_avg_power_kw`, `night_samples`, `night_valid`, and `night_battery_adjusted`. `night_avg_power_kw` is `night_energy_kwh / night_duration_h`, not a time-of-day profile. `night_date_ordinal` is an internal numeric encoding of the night date used to reconstruct the record from InfluxDB.

The reserve uses the latest valid night for each weekday in `selection`, weighted by `sum(energy_wh) / sum(duration_h)`, multiplied by `safety_factor`. Only nights strictly before the simulated/current evening are eligible. Historical household references are already gross, so `--backfill-days` always aggregates raw history as gross, independent of `household_meter_includes_battery`. Live input meters include battery flow and are corrected with actual measured battery power before a setpoint is calculated. The selected baseline is cached in memory, so the control loop does not query the database. Missing or invalid nights fall back to `baseline_household_w` without stopping control. With `enabled: false`, the fixed value is used and no baseline query or backfill is needed.

Backfill stored nights and inspect a selection from the repository root:

```bash
poetry run bems-night-baseline --backfill-days 14
poetry run bems-night-baseline --show --night-date 2026-11-18
```

Live control stores `night_baseline_power` in kW and `battery_night_reserve_kwh` in kWh on the `bems_live_<device-id>` source. The reserve estimates household baseline energy through the night; the controller uses it as the battery energy floor. It is not a heat-pump-only reserve. Historical simulation JSONL files retain internal W/Wh values for report calculations.

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

This runs the live controller loop against current MQTT meter measurements while replacing the battery adapters with in-memory mock batteries. It is useful to observe requested power and controller measurements without sending commands to hardware.

1. Temporarily enable a `mode: live` device in `config.yaml`. Its adapter attributes are ignored by `--mock`.
2. Ensure HEMS is publishing fresh grid and household measurements to the MQTT broker.
3. Run one safe cycle:

   ```bash
   poetry run bems-live --mock --once
   ```

4. Run continuously by omitting `--once`:

   ```bash
   poetry run bems-live --mock
   ```

The mock starts at the device's configured `initial_soc_percent`; its state is in memory and resets when the process restarts. It integrates charging and discharging over elapsed time with the configured efficiency and SOC limits. Every accepted setpoint is appended to `data/bems_mock_commands.csv` by default. Use `--mock-csv <path>` to choose another file. The CSV contains `timestamp`, `device_id`, `requested_power_w`, `accepted_power_w`, and `soc_percent`, and is written on exit (including Ctrl-C), so it can be plotted without querying InfluxDB. Live-loop measurements use source `bems_live_<device-id>` and include requested/actual power, SOC, energy, input freshness, device availability, and firmware/control status. This mode does not calculate historical cost savings; use `bems-simulate` for that comparison.

## Separate BEMS mock and command log

The separate BEMS container can host the live controller against a mock battery. Set `attributes.mock: true` on an enabled `mode: live` device; no vendor, host, or `allow_control` setting is required. Mock meter values remain the battery-free household load, so BEMS does not subtract simulated battery delivery from them a second time. Real `mode: live` meter readings include battery effects and continue to be compensated using measured battery power.

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

Set `bems.control.command_log_enabled: true` to append every setpoint actually sent to an adapter:

```yaml
bems:
  control:
    command_log_enabled: true
    command_log_csv: data/bems_commands.csv
```

Set `command_log_enabled: false` to disable the CSV log.

The CSV columns are `timestamp`, `device_id`, `requested_power_w`, `accepted_power_w`, `soc_percent`, `status`, and `error`. `status` is `accepted` when the adapter state was read after the write, `sent` when the write succeeded but state could not be read, and `failed` when the write itself failed. The live service appends and flushes each row, so commands survive a crash. This log works for both mock and real adapters. Without hardware, an Anker adapter fails at identity/state preflight before `set_power`, so no command row is produced; use `attributes.mock: true` with the Anker profile to exercise the control path before the battery arrives.

The live service writes battery metrics to InfluxDB under `bems_live_<device-id>` with a `battery_` prefix. Power values (`battery_requested_power`, `battery_actual_power`, `battery_command_requested_power`, and `battery_command_reported_power`) are stored in kW, and battery energies (`battery_energy_stored`, `battery_night_reserve_kwh`) in kWh, matching HEMS meter units; BEMS converts them from internal W and Wh values. SOC remains in percent. Problem indicators are event-style: `battery_stale_input=1` is written only for stale input, `battery_control_ok=0` only when control is blocked or fails, and `battery_device_alive=0` only when the adapter cannot read state. Successful states are assumed; no healthy `1` points are written. Each attempted command adds a requested power, a reported power when readback is available, and status (`1` accepted, `0` sent but unreadable, `-1` failed). Firmware compatibility remains enforced by the controller but is not stored in InfluxDB. The `bems-live` process also honors `bems.control.command_log_enabled`/`command_log_csv`; its `--mock-csv` export remains available as a separate one-file plotting snapshot with W-valued columns.

## Live feed and stale-input safety

Live control runs in the separate BEMS container and subscribes to HEMS meter events over MQTT. The HEMS publisher starts at one event per second for each configured meter series. This MQTT path is independent of `storage_interval`, which remains unchanged for InfluxDB history. Only grid and household meter values are published; PV remains available to historical reports and is not required by live control. Both containers read the same root `config.yaml`; HEMS uses collector/calculator/storage settings, while BEMS uses the `bems` section.

The live BEMS watches `config.yaml` and applies numeric control and power-limit changes without restarting, including global/device/profile charge and discharge limits. The live-device set, device identity/connection attributes, selected profile, and battery capacity are fixed for the process lifetime; changing those requires restarting BEMS.

Two different timeouts exist:

- `max_measurement_age_s` is simulation/replay only. Historical runs use it to detect input gaps before integrating energy.
- `live_max_measurement_age_s` (default 3 s) is live only. If no fresh grid sample arrives within that window, the controller requests 0 W from every live battery. A 0 W target deactivates both charging and feed-in; the command is attempted even when identity/firmware preflight is unavailable.

If either meter stream is missing or stale, BEMS requests 0 W. The MQTT broker is a separate `mqtt` container, isolated on the Compose network and protected with separate HEMS-publisher and BEMS-subscriber credentials.

`hems_mqtt_publish_interval_s` limits how often HEMS publishes meter values. BEMS MQTT control is event-driven and runs when a new grid message arrives; it has no separate MQTT polling interval. `bems_db_poll_interval_s` applies only when BEMS uses the optional database feed and does not need to match the HEMS publish interval.

## Live controller with a real battery

Live hardware adapters exist for the Zendure SolarFlow ZenSDK API, Marstek Venus E 3.0, and Anker SOLIX Solarbank Max AC. Hardware writes are opt-in and compatibility-gated.

1. Configure the live device's profile, measured device IP, vendor, and transport settings. Keep `enabled: false` until setup and identity checks are complete.
2. Read the device's exact reported model and firmware using the vendor adapter's read-only checks or a mock/dry setup. Set `expected_model` and `supported_firmware_versions` to explicit verified values; the firmware list must contain the exact version token.
3. Verify the correct network protocol and enable the device's local control feature where required. Zendure uses the local ZenSDK HTTP API; Anker uses Modbus TCP; Marstek uses its local UDP API.
4. Set `attributes.allow_control: true`, then `enabled: true` only when you intend to permit hardware writes. Missing identity allowlists, mismatched model/firmware, communication failure, or stale meter readings block or stop power requests.
5. Start the controller:

   - Start the `hems`, `bems`, `mqtt`, and `influxdb` Compose services. HEMS publishes the two meter streams; BEMS controls the battery independently. Set `MQTT_HEMS_PASSWORD` and `MQTT_BEMS_PASSWORD` in the root `.env` first.
   - For a one-shot diagnostic with a mock battery, run:

   ```bash
   poetry run bems-live
   ```

6. Monitor `bems_live_<device-id>` for `battery_requested_power`, `battery_actual_power`, `battery_soc`, `battery_energy_stored`, and `battery_night_reserve_kwh`. Error indicators `battery_device_alive=0`, `battery_control_ok=0`, or `battery_stale_input=1` appear only when a problem occurs.

The live service is always a separate process from HEMS monitoring. Do not enable more than one live controller for the same physical battery.

## Measurement names

| Source | Example metrics | Purpose |
| --- | --- | --- |
| `bems_sim_<device-id>_<run-id>` | `battery_requested_power`, `battery_actual_power`, `battery_soc`, `battery_energy_stored`, `charge_loss_total`, `discharge_loss_total`, `savings_total` | Local simulation JSONL output; power/energy values use internal W/Wh |
| `bems_live_<device-id>` | `battery_requested_power`, `battery_actual_power`, `battery_soc`, `battery_energy_stored`, `battery_device_alive`, `battery_control_ok`, `battery_stale_input`, `battery_command_*` | Live battery readings, commands, and control status |

## Tests and checks

```bash
poetry run pytest test/bems -q
poetry run ruff check .
poetry run pyrefly check
poetry run ruff format .
```
