# Battery Energy Management System (BEMS)

The BEMS controls and evaluates AC-coupled batteries independently of the HEMS polling loop. It supports historical simulations, live control with an in-memory battery, and live control through Anker or Marstek adapters.

## What it does

- Reads grid, household, and PV power from the configured database.
- Uses import-positive power internally; HEMS meter values are normalized at the BEMS boundary.
- Charges only from measured grid export (PV surplus).
- Serves household demand first. Heat-pump demand is served only from energy above the calculated overnight reserve.
- Limits power by battery profile, per-device setting, and the global BEMS limit.
- Models charge/discharge efficiency, power ramping, SOC limits, and energy losses in historical simulations.
- Stores requested power separately from actual power and state measurements.
- Requires an available device and an explicitly approved model/firmware before live hardware writes.

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
4. Inspect measurements written under `bems_sim_<device-id>_<run-id>`. They include requested/actual power, SOC, stored energy, cumulative losses, reference and battery costs, and savings.
5. To continue a saved run with the same ID, pass both `--run-id <id>` and `--resume`. The simulation restores its last persisted checkpoint.

Each device is simulated independently against the same meter history. A restart begins from its configured initial SOC unless resuming a run.

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

The mock starts at the device's configured `initial_soc_percent`; its state is in memory and resets when the process restarts. Live-loop measurements use source `bems_live_<device-id>` and include requested/actual power, SOC, energy, input freshness, device availability, and firmware/control status. This mode does not calculate historical cost savings; use `bems-simulate` for that comparison.

## Live controller with a real battery

Live hardware adapters exist for the Marstek Venus E 3.0 and Anker SOLIX Solarbank Max AC. Hardware writes are opt-in and compatibility-gated.

1. Configure the live device's profile, measured device IP, vendor, and transport settings. Keep `enabled: false` until setup and identity checks are complete.
2. Read the device's exact reported model and firmware using the vendor adapter's read-only checks or a mock/dry setup. Set `expected_model` and `supported_firmware_versions` to explicit verified values; the firmware list must contain the exact version token.
3. Verify the correct network protocol and enable the device's local control feature where required. Anker uses Modbus TCP; Marstek uses its local UDP API.
4. Set `attributes.allow_control: true`, then `enabled: true` only when you intend to permit hardware writes. Missing identity allowlists, mismatched model/firmware, communication failure, or stale meter readings block or stop power requests.
5. Start the independent controller process:

   ```bash
   poetry run bems-live
   ```

   Use `poetry run bems-live --once` for a single poll cycle. With an enabled real device this can send a hardware command; `--mock` never sends vendor commands.
6. Monitor `bems_live_<device-id>` for `requested_power`, `actual_power`, `soc`, `device_alive`, `firmware_compatible`, `control_ok`, and `stale_input`.

The live service is a separate process from HEMS monitoring. Start it as a separate service/container if it should run continuously. Do not enable more than one live controller for the same physical battery.

## Measurement names

| Source | Example metrics | Purpose |
| --- | --- | --- |
| `bems_sim_<device-id>_<run-id>` | `requested_power`, `actual_power`, `soc`, `energy_stored`, `charge_loss_total`, `discharge_loss_total`, `savings_total` | Per-run historical simulation and checkpoints |
| `bems_live_<device-id>` | `requested_power`, `actual_power`, `soc`, `energy_stored`, `device_alive`, `firmware_compatible`, `control_ok`, `stale_input` | Live controller/device status |

## Tests and checks

```bash
poetry run pytest test/bems -q
poetry run ruff check .
poetry run pyrefly check
poetry run ruff format .
```
