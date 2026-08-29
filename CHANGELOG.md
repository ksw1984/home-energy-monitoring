# Changelog

All notable changes to this project will be documented in this file.

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
