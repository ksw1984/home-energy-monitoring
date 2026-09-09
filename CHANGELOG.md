# Changelog

All notable changes to this project will be documented in this file.

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
