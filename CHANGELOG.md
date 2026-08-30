# Changelog

All notable changes to this project will be documented in this file.

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
- Deployment: Add a production/dev deployment compose and environment example (deployment/compose.yml, deployment/.env.example).
- Release automation: Release workflow now outputs version and tag and triggers a build-container job as part of the release flow (.github/workflows/release-main.yml).

### Changed
- Release pipeline: release-main.yml refactored/extended to:
  - export release outputs (version, tag),
  - prepare/reuse release notes file,
  - create/push release tag and GitHub Release,
  - invoke a container build job after release.
- CI/workflows: reorganized workflow responsibilities between prepare-release, release-main, and the new build-container workflow.

### Removed
- Top-level compose.yml removed in favor of deployment/compose.yml (deployment split & reorganization).

### Notes
- These changes add container image build & publishing and an explicit deployment layout. Recommended next steps before publishing a new release:
  1. Bump project version (e.g., 0.1.1) in `pyproject.toml`.
  2. Add a release section for the new version in CHANGELOG.md (move the above "Unreleased" into `## 0.1.1 - YYYY-MM-DD` when ready).
  3. Merge dev → main (release PR) so the release-main workflow can create the tag and publish artifacts (the new build-container job will then build/push the container).


## 0.1.0 - 2026-08-29
First version with basic functionality to monitor grid meters, pv inverter, environment sensor, weather reports.

### Added
- **Collector Factory** - Dynamic collector pattern implementation for flexible data collection ([#39](https://github.com/ksw1984/home-energy-monitoring/pull/39))
- **Weather Forecast Integration** - Added weather forecast data collection capabilities ([#30](https://github.com/ksw1984/home-energy-monitoring/pull/30))
- **Dual Meter Support** - Added second meter for household vs grid energy tracking ([#26](https://github.com/ksw1984/home-energy-monitoring/pull/26))
- **Time-Dependent Inverter Data** - Collect time-dependent power and energy data from Fronius inverter ([#14](https://github.com/ksw1984/home-energy-monitoring/pull/14))
- **Rademacher Environment Sensor** - Data collection from Rademacher Umweltsensor ([#13](https://github.com/ksw1984/home-energy-monitoring/pull/13))
- **Fronius Inverter Integration** - Basic Fronius inverter data retrieval ([#6](https://github.com/ksw1984/home-energy-monitoring/pull/6))
- **Persistent Intermediate Storage** - Data persistence layer for reliable collection ([#8](https://github.com/ksw1984/home-energy-monitoring/pull/8))
- **Grafana & InfluxDB Integration** - Docker support and local database testing ([#10](https://github.com/ksw1984/home-energy-monitoring/pull/10))
- **Daily/Monthly Meter Reporting** - Log meter readings at 000 for reporting purposes ([#36](https://github.com/ksw1984/home-energy-monitoring/pull/36))

### Improved
- **Logging** - Integrated comprehensive logging throughout the application ([#37](https://github.com/ksw1984/home-energy-monitoring/pull/37))
- **Code Quality** - Workflow setup with pre-commit hooks, ruff, black, and mypy configuration ([#19](https://github.com/ksw1984/home-energy-monitoring/pull/19), [#22](https://github.com/ksw1984/home-energy-monitoring/pull/22), [#21](https://github.com/ksw1984/home-energy-monitoring/pull/21))
- **Test Coverage** - Improved test coverage ([#31](https://github.com/ksw1984/home-energy-monitoring/pull/31))

### Other
- **Documentation** - Added badges to README ([#20](https://github.com/ksw1984/home-energy-monitoring/pull/20))
- **Project Setup** - Initial basic project setup ([#5](https://github.com/ksw1984/home-energy-monitoring/pull/5))
