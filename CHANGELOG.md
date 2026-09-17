# Changelog

All notable changes to this project will be documented in this file.

## 0.8.0 - 2026-09-17

### Added

- Measurement processing now keeps the latest measurement for each source and metric to support timestamp alignment
  across multiple inputs.
- Kalman estimators now support configurable lookback-based estimation.
- Formula calculations can combine measurements from multiple sources using aligned timestamps.
- Added inverter efficiency calculation support using AC power and MPPT DC power measurements.
- Added integration-test documentation for building, exporting, and running the ARM64 Docker image on a Raspberry Pi.
- Added handling for stale IEC telegrams while waiting for a valid identification response.

### Changed

- Estimator API redesigned:
    - Replace `estimate()` with `add_measurements()`, `target_timestamp()`, and `estimate_at()`.
    - Replace latency and history-size configuration with a numeric `lookback` value.
    - Estimators are now created automatically for measurements required by multi-source calculations.
- Kalman estimation changed from time-based latency to sample-based lookback:
    - `latency` was replaced by `lookback`.
    - `history_size` was removed.
    - Estimator history is now maintained without a fixed deque size.
- Measurement manager now:
    - Stores the latest measurement for each source and metric.
    - Aligns calculator inputs to a common timestamp.
    - Uses estimator output when calculations require measurements from multiple sources.
    - Processes calculator inputs only when relevant new measurements arrive.
- Formula calculations now skip calculations that would cause division by zero and log the skipped calculation at debug
  level.
- Calculator output status ordering changed so calculated and estimated markers are displayed before the
  selected-measurement marker.
- Estimator creation now derives required inputs from enabled calculator configurations instead of explicit estimator
  measurement lists.
- Configuration for estimators simplified:
    - Removed duration parsing and latency configuration.
    - Removed explicit estimator measurement lists.
    - Added `lookback` configuration.
- Configuration examples updated:
    - Heat-pump power now accounts for grid import, grid export, household import, and household export.
    - Formula units and expressions were updated to use kilowatts.
    - Kalman estimator configuration now uses `lookback`.
- IEC identification handling improved:
    - Ignores stale or incomplete telegram data.
    - Retries the identification request after detecting stale data.
    - Returns only a valid identification response.
- README expanded with local, CI, and Raspberry Pi integration-test instructions.
- README formatting and Docker command documentation improved.

### Fixed

- Formula calculations no longer fail when a denominator is zero.
- Multi-source calculations now avoid mixing measurements from different timestamps.
- Estimator and calculator integration tests updated to reflect the new lookback and timestamp-alignment behavior.
- IEC protocol handling now raises a clear error when no valid identification response is received.
- Configuration and application tests updated for the new estimator and calculator interfaces.
- Database cleanup in the application now closes every configured database instance.
-

## 0.7.0 - 2026-09-13

### Summary

- Major runtime and configuration refactor: per-collector runtime configuration, persistent-storage filtering, and
  hot-reload of config.
- Deeper Fronius support: SunSpec / Modbus measurements (MPPT, AC totals) in addition to REST API.
- IEC meter reliability and protocol hardening: persistent serial connection, robust session handling and timeouts.
- Collector manager rewritten to run collectors independently (per-collector intervals) and to be more resilient to
  individual failures.
- App lifecycle and shutdown improved (signal handling, graceful cancel/cleanup).
- Tests expanded and updated to cover new behaviour and SunSpec data.
- Tooling / CI updates (ruff/pyrefly, pre-commit changes), small packaging updates.

### Added

- Storage configuration and filtering: new storage section support and StorageFilter to persist only configured
  measurement keys (src/config/config.py, src/config/storage_filter.py).
- SunSpec / Modbus Fronius data collection: MPPT and AC total metrics via SunSpec/Modbus in addition to existing Fronius
  REST data (src/collectors/fronius_inverter/*, src/collectors/definitions/fronius.py).
- New IEC tooling script to test serial connection lifetime and handshake behavior (
  src/scripts/check_iec_connection_timeout.py).
- More collector runtime metadata (collector.source, interval, enabled) and a storage_key property on Measurement for
  precise storage control (src/collectors/definitions/measurement.py, src/collectors/base_collector.py).
- Config package: move configuration loading/structures to src/config/config.py with richer typed dataclasses and
  storage mappings.
- Collector runtime reconfiguration: Manager can update collector runtime (interval/enabled) from config file changes.

### Changed

- CollectorManager rework:
    - Collectors now run independently in their own loop/task, honoring per-collector intervals and enabling better
      isolation of failures (src/manager/collector_manager.py).
    - Config file changes are detected and applied at runtime (intervals/enabled/storage); storage filter updated on
      change.
    - Daily energy snapshot logic now tracked per-source and is concurrency-safe.
- App lifecycle:
    - Added signal handling and controlled shutdown with task cancellation; explicit event loop handling to allow
      graceful cleanup (src/app.py).
- Collectors:
    - BaseCollector receives structured runtime args (timezone, interval, enabled, source) and added configure_runtime()
      helper (src/collectors/base_collector.py).
    - Collector factory uses common kwargs and honors collector-specific interval (src/collectors/collector_factory.py).
    - Open-Meteo, Rademacher, IEC collectors made timezone-aware and accept runtime source/interval flags (
      src/collectors/*).
- IEC protocol & collector:
    - IEC implementation improved: always re-uses the same physical serial connection, uses a consistent session flow,
      increased robustness to empty reads, explicit timeouts (src/collectors/iec/iec_protocol.py).
    - IecCollector now tracks connection state, opens lazily, and will mark itself disconnected if serial errors occur
      so subsequent cycles can attempt reconnect (src/collectors/iec/iec_collector.py).
- Databases:
    - InfluxDB point construction compacted; TextFile DB improved typing and timezone handling (
      src/databases/influxdb/influxdb.py, src/databases/text_file/textfiledb.py).
    - Database factory typing improvements and wiring (src/databases/database_factory.py).
- Tests:
    - Tests updated and extended (SunSpec/MPPT, IEC session behavior, manager behavior, config/storage), renamed/moved
      scripts/tests accordingly (test/*).
- Tooling and CI:
    - Bumped lint/type tooling versions and moved MyPy -> Pyrefly in CI / pre-commit; ruff formatting/lint
      improvements (pyproject, .pre-commit-config.yaml, .github/workflows/*).
    - Added pyrefly configuration and dev dependency (pyproject.toml).

### Fixed

- Collector fault-isolation: failures from a single database.store() or one collector do not stop the rest of the
  system; errors are logged and other stores/collectors continue.
- IEC protocol: improved payload extraction, robust reads, and deterministic timeouts to avoid hangs on bad serial
  devices.
- Fronius collector: gracefully handles missing REST fields now and treats None as zero where appropriate; SunSpec
  collection errors are wrapped in a dedicated exception class.
- Tests: fixed tests to assert timezone-aware datetimes and to reflect new per-collector behaviour.

## 0.6.0 - 2026-09-09

### Added

- CI: Add a deterministic CI dependency image workflow to build/push a cached "deps" image for CI (
  /.github/workflows/build-ci-image.yml).
- CI: Add a reusable "Build Container" workflow and Docker app/CI Dockerfiles to produce release images (
  /.github/workflows/build-container.yml, docker/app/Dockerfile, docker/ci/Dockerfile).
- CI: Containerized CI runners — CI jobs now use a pre-built CI image (ghcr.io/.../home-energy-monitoring-ci:latest) to
  speed up checks (Black, Ruff, MyPy, Pytest).
- Build: Multi-platform container builds enabled (linux/amd64, linux/arm64) using Buildx and QEMU in container build
  workflow.
- Deployment: Add production/dev deployment layout and example env (deployment/compose.yml, deployment/.env.example) and
  helper commands (docker/cmds.txt).
- Database: Add a TextFile database backend that persists measurements as per-day JSON Lines (
  src/databases/text_file/textfiledb.py) and register it in database factory.
- Telemetry: Add timezone config for collection and propagate it into collectors (config.yaml + src/config.py,
  BaseCollector timezone support).
- Collectors: Make collectors timezone-aware and use configured timezone when producing timestamps (Fronius, IEC,
  Rademacher, Open-Meteo collectors updated).
- Tests: Add tests for the new TextFile database and update existing tests to assert timezone handling and collector
  factory timezone propagation (test/…).
- Docs: Expand README with architecture, release workflow, and usage; add docker/cmds.txt for common commands.

### Changed

- Release automation: release workflows improved to export version/tag outputs and trigger container builds as part of
  the release flow (.github/workflows/prepare-release-pr.yml, .github/workflows/release-main.yml).
- CI workflows: CI now builds/uses a CI dependency image (build-ci-image) and references newer docker actions (
  build-push v7, login v4); workflows simplified by running linters/tests inside the CI container.
- Collector factory & config: collection timezone added and passed to collector constructors; collectors now localize
  timestamps using the configured timezone.
- Code quality / robustness:
    - Collector manager now continues storing measurements to remaining databases when one database.store() fails and
      logs exceptions (src/manager/collector_manager.py).
    - InfluxDB write logging fixed to log points correctly (src/databases/influxdb/influxdb.py).
    - Safer resource handling in TextFile database and other components.
- Project layout: application Dockerfile split into builder/runtime stages and project Docker layout reorganized (
  docker/app, docker/ci).

### Fixed

- Tests and code updated to use timezone-aware timestamps consistently (various tests updated).
- Minor logging and formatting fixes (including safer logging calls).

### Removed

- Top-level compose.yml replaced by the deployment/compose.yml layout (compose.yml deleted).

### Documentation

- README expanded with architecture, CI/release flow, deployment and local Docker instructions.
- Add docker/cmds.txt (quick Docker Compose commands) and deployment examples.

### Notes and release guidance

- The repository's pyproject.toml was previously updated to 0.5.0 — for this release please:
    1. Bump version in pyproject.toml to 0.6.0.
    2. Add the section above to CHANGELOG.md under the new `## 0.6.0 - YYYY-MM-DD` heading (move/adjust Unreleased
       section if present).
    3. Ensure CHANGELOG.md contains a short release notes paragraph (the "Suggested release summary" below is suitable).
    4. Create a release PR/dev→main and merge; the release workflow will tag and build/publish images.

Suggested short release summary (for GitHub Release notes):

- "Add CI dependency image, multi-platform container builds, timezone-aware collectors, a text-file JSONL database
  backend, and deployment compose templates. CI and release

## 0.5.0 - 2026-08-31

### Added

- Added multi platform build for linux and arm

## 0.4.0 - 2026-08-31

### Added

- Added using same timezone in measurements

## 0.3.0 - 2026-08-31

### Added

- Added TextFile based "database" to collect json lines formatted measurements.

## 0.2.3 - 2026-08-30

### Fixed

- Docker: Fix entry point

## 0.2.2 - 2026-08-30

### Added

- CI: Add required Readme to container for install
-

## 0.2.1 - 2026-08-30

### Added

- CI: Add required files

## 0.2.0 - 2026-08-30

### Added

- CI: Add a Build Container workflow to build & push container images to GHCR (/.github/workflows/build-container.yml).
- Deployment: Add a production/dev deployment compose and environment example (deployment/compose.yml,
  deployment/.env.example).
- Release automation: Release workflow now outputs version and tag and triggers a build-container job as part of the
  release flow (.github/workflows/release-main.yml).

### Changed

- Release pipeline: release-main.yml refactored/extended to:
    - export release outputs (version, tag),
    - prepare/reuse release notes file,
    - create/push release tag and GitHub Release,
    - invoke a container build job after release.
- CI/workflows: reorganized workflow responsibilities between prepare-release, release-main, and the new build-container
  workflow.

### Removed

- Top-level compose.yml removed in favor of deployment/compose.yml (deployment split & reorganization).

### Notes

- These changes add container image build & publishing and an explicit deployment layout. Recommended next steps before
  publishing a new release:
    1. Bump project version (e.g., 0.1.1) in `pyproject.toml`.
    2. Add a release section for the new version in CHANGELOG.md (move the above "Unreleased" into
       `## 0.1.1 - YYYY-MM-DD` when ready).
    3. Merge dev → main (release PR) so the release-main workflow can create the tag and publish artifacts (the new
       build-container job will then build/push the container).

## 0.1.0 - 2026-08-29

First version with basic functionality to monitor grid meters, pv inverter, environment sensor, weather reports.

### Added

- **Collector Factory** - Dynamic collector pattern implementation for flexible data
  collection ([#39](https://github.com/ksw1984/home-energy-monitoring/pull/39))
- **Weather Forecast Integration** - Added weather forecast data collection
  capabilities ([#30](https://github.com/ksw1984/home-energy-monitoring/pull/30))
- **Dual Meter Support** - Added second meter for household vs grid energy
  tracking ([#26](https://github.com/ksw1984/home-energy-monitoring/pull/26))
- **Time-Dependent Inverter Data** - Collect time-dependent power and energy data from Fronius
  inverter ([#14](https://github.com/ksw1984/home-energy-monitoring/pull/14))
- **Rademacher Environment Sensor** - Data collection from Rademacher
  Umweltsensor ([#13](https://github.com/ksw1984/home-energy-monitoring/pull/13))
- **Fronius Inverter Integration** - Basic Fronius inverter data
  retrieval ([#6](https://github.com/ksw1984/home-energy-monitoring/pull/6))
- **Persistent Intermediate Storage** - Data persistence layer for reliable
  collection ([#8](https://github.com/ksw1984/home-energy-monitoring/pull/8))
- **Grafana & InfluxDB Integration** - Docker support and local database
  testing ([#10](https://github.com/ksw1984/home-energy-monitoring/pull/10))
- **Daily/Monthly Meter Reporting** - Log meter readings at 000 for reporting
  purposes ([#36](https://github.com/ksw1984/home-energy-monitoring/pull/36))

### Improved

- **Logging** - Integrated comprehensive logging throughout the
  application ([#37](https://github.com/ksw1984/home-energy-monitoring/pull/37))
- **Code Quality** - Workflow setup with pre-commit hooks, ruff, black, and mypy
  configuration ([#19](https://github.com/ksw1984/home-energy-monitoring/pull/19), [#22](https://github.com/ksw1984/home-energy-monitoring/pull/22), [#21](https://github.com/ksw1984/home-energy-monitoring/pull/21))
- **Test Coverage** - Improved test coverage ([#31](https://github.com/ksw1984/home-energy-monitoring/pull/31))

### Other

- **Documentation** - Added badges to README ([#20](https://github.com/ksw1984/home-energy-monitoring/pull/20))
- **Project Setup** - Initial basic project setup ([#5](https://github.com/ksw1984/home-energy-monitoring/pull/5))
