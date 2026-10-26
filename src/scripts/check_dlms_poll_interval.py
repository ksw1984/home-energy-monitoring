"""Probe the shortest stable DLMS polling period for a Landis+Gyr meter.

The probe keeps one persistent DLMS connection and repeatedly reads only the
total active power register (``16.7.0``). By default it walks a 0.01 s ladder
from 0.40 s down to 0.05 s, taking 10 reads per candidate period. It stops at
the first unstable period. An optional refinement pass can reconnect, restart
above that period and descend again in 0.01 s steps. It reports two numbers:

* the shortest period whose reads all finish before the next scheduled start
  with at least 10 % headroom, and
* the estimated register update period derived from observed value changes.
  A meter can answer faster than it updates, so the readable period and the
  useful measurement period are not necessarily the same.

The scheduling rule mirrors ``MeasurementManager._run_collector``: sleep until
the target start, then compensate for the read duration. That keeps the
measured numbers transferable to production polling.

Run the probe with the production application stopped, because the serial port
is exclusive::

    poetry run python -m src.scripts.check_dlms_poll_interval --port /dev/ttyUSB0
"""

from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

from src.collectors.landys_gyr_zmd310_meter.dlms.dlms_collector import DlmsCollector

import serial

logger = logging.getLogger("dlms-poll-interval-probe")

# The probe deliberately reads one register only. Reading the energy totals as
# well would make the result depend on the register set and hide the real
# latency of the power value that BEMS and HEMS need.
PROBE_OBIS = "16.7.0"
PROBE_METRIC = "grid_active_power"
PROBE_SOURCE = "dlms_poll_probe"

DEFAULT_PORT = "/dev/ttyUSB0"
# 0.40 s down to 0.05 s in 0.01 s steps. The threshold only separates the two
# ladder phases; both use the same step so the result is one continuous ladder.
DEFAULT_START_PERIOD_S = 0.40
DEFAULT_THRESHOLD_PERIOD_S = 0.39
DEFAULT_COARSE_STEP_S = 0.01
DEFAULT_MEDIUM_STEP_S = 0.01
DEFAULT_BASE_END_PERIOD_S = 0.05
DEFAULT_REFINE_OFFSET_S = 0.2
DEFAULT_REFINE_STEP_S = 0.01
DEFAULT_FLOOR_PERIOD_S = 0.05

# Ten reads are the minimum that still shows a single outlier reliably.
MIN_SAMPLES_PER_STEP = 10
WARMUP_READS = 5
SETTLE_SAMPLES = 2

# A period is stable when the slowest read leaves at least 10 % headroom.
STABILITY_READ_HEADROOM = 0.9

# Bound one reply to roughly two periods. Without this a dead meter would block
# for the protocol default of 30 s per read and turn a failed step into a
# multi-minute hang.
MIN_READ_TIMEOUT_S = 1.0
READ_TIMEOUT_FACTOR = 2.0

PERIOD_DECIMALS = 3
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data"

EXIT_OK = 0
EXIT_NO_STABLE_PERIOD = 2
EXIT_CONNECTION_FAILED = 3
EXIT_INTERRUPTED = 130

CSV_FIELD_NAMES = (
    "period_s",
    "index",
    "scheduled_offset_s",
    "actual_offset_s",
    "achieved_interval_s",
    "read_duration_s",
    "deadline_missed",
    "value",
    "error",
    "warmup",
)


class ProbeConnectionError(RuntimeError):
    """Raised when the probe cannot open or recover the DLMS connection."""


@dataclass(frozen=True)
class ProbeSample:
    """One scheduled read inside a candidate period.

    All offsets are seconds relative to the first scheduled read of the step.
    ``achieved_interval_s`` is ``None`` for the first read and for reads that
    follow a reconnect, because the previous start is not comparable there.
    """

    period_s: float
    index: int
    scheduled_offset_s: float
    actual_offset_s: float
    achieved_interval_s: float | None
    read_duration_s: float | None
    deadline_missed: bool
    value: float | None
    error: str | None
    warmup: bool = False


@dataclass(frozen=True)
class StepSummary:
    """Aggregated result for one candidate period."""

    period_s: float
    ok: int
    failed: int
    warmup: int
    read_median_s: float | None
    read_max_s: float | None
    achieved_median_s: float | None
    deadline_misses: int
    distinct_values: int
    value_change_median_s: float | None
    stable: bool


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a full ladder run."""

    periods: list[float]
    refine_periods: list[float]
    summaries: list[StepSummary]
    minimum_stable_s: float | None
    first_unstable_s: float | None
    csv_path: Path


class CsvRecorder:
    """Append probe samples to a CSV file while the probe is running.

    Rows are flushed immediately so an interrupted run still leaves usable
    data for analysis.

    Args:
        path: Destination CSV file. Parent directories are created.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELD_NAMES)
        self._writer.writeheader()

    def write(self, sample: ProbeSample) -> None:
        """Append one sample row and flush it to disk."""
        self._writer.writerow(
            {
                "period_s": sample.period_s,
                "index": sample.index,
                "scheduled_offset_s": f"{sample.scheduled_offset_s:.4f}",
                "actual_offset_s": f"{sample.actual_offset_s:.4f}",
                "achieved_interval_s": "" if sample.achieved_interval_s is None else f"{sample.achieved_interval_s:.4f}",
                "read_duration_s": "" if sample.read_duration_s is None else f"{sample.read_duration_s:.4f}",
                "deadline_missed": int(sample.deadline_missed),
                "value": "" if sample.value is None else f"{sample.value:.6f}",
                "error": sample.error or "",
                "warmup": int(sample.warmup),
            }
        )
        self._file.flush()

    def close(self) -> None:
        """Close the underlying file."""
        self._file.close()


def build_period_ladder(
    *,
    start_s: float = DEFAULT_START_PERIOD_S,
    threshold_s: float = DEFAULT_THRESHOLD_PERIOD_S,
    coarse_step_s: float = DEFAULT_COARSE_STEP_S,
    medium_step_s: float = DEFAULT_MEDIUM_STEP_S,
    base_end_s: float = DEFAULT_BASE_END_PERIOD_S,
) -> list[float]:
    """Build the descending coarse and medium ladder.

    The coarse phase runs from ``start_s`` down to ``threshold_s`` with
    ``coarse_step_s``; the medium phase continues below the threshold with
    ``medium_step_s`` until ``base_end_s``. The fine search around the first
    failure is a separate ladder, see :func:`build_refine_ladder`.

    Values are rounded to milliseconds so float noise cannot produce duplicate
    or out-of-order steps.

    Args:
        start_s: Longest period to test.
        threshold_s: Period at which the ladder switches to the medium step.
        coarse_step_s: Decrement above ``threshold_s``.
        medium_step_s: Decrement below ``threshold_s``.
        base_end_s: Shortest period of the base ladder.

    Returns:
        Descending, duplicate-free list of candidate periods.

    Raises:
        ValueError: If the bounds or steps cannot produce a valid ladder.
    """
    if start_s <= threshold_s:
        raise ValueError("start_s must be greater than threshold_s")
    if coarse_step_s <= 0 or medium_step_s <= 0:
        raise ValueError("steps must be positive")
    if base_end_s <= 0 or base_end_s >= threshold_s:
        raise ValueError("base_end_s must be positive and below threshold_s")
    if threshold_s - medium_step_s < base_end_s:
        raise ValueError("medium_step_s is too large for the configured range")

    coarse_count = round((start_s - threshold_s) / coarse_step_s)
    periods = [round(start_s - index * coarse_step_s, PERIOD_DECIMALS) for index in range(coarse_count + 1)]

    medium_start = round(threshold_s - medium_step_s, PERIOD_DECIMALS)
    medium_count = round((medium_start - base_end_s) / medium_step_s)
    periods.extend(round(medium_start - index * medium_step_s, PERIOD_DECIMALS) for index in range(medium_count + 1))

    ladder: list[float] = []
    for period in periods:
        if period < base_end_s:
            continue
        if ladder and period >= ladder[-1]:
            continue
        ladder.append(period)

    if not ladder:
        raise ValueError("period ladder is empty")

    return ladder


def build_refine_ladder(
    *,
    failure_s: float,
    offset_s: float = DEFAULT_REFINE_OFFSET_S,
    step_s: float = DEFAULT_REFINE_STEP_S,
    floor_s: float = DEFAULT_FLOOR_PERIOD_S,
    ceiling_s: float | None = None,
) -> list[float]:
    """Build the fine ladder used around the first unstable period.

    The search restarts ``offset_s`` above the failed period and descends in
    ``step_s`` steps until ``floor_s``. When the offset would land above a
    period that was already stable, the climb is capped at that stable period:
    retesting a known-good point is useful, testing a longer period than the
    base ladder is not.

    Args:
        failure_s: First period that was not stable.
        offset_s: How far above ``failure_s`` the fine search starts.
        step_s: Decrement inside the fine search.
        floor_s: Shortest period to test.
        ceiling_s: Last stable period of the base ladder, if any.

    Returns:
        Descending list of fine periods, empty when the range is invalid.

    Raises:
        ValueError: If the steps are not positive.
    """
    if offset_s <= 0 or step_s <= 0:
        raise ValueError("refine offset and step must be positive")
    if floor_s <= 0:
        raise ValueError("floor_s must be positive")

    start_s = round(failure_s + offset_s, PERIOD_DECIMALS)

    if ceiling_s is not None:
        start_s = min(start_s, ceiling_s)

    if start_s < floor_s:
        return []

    count = round((start_s - floor_s) / step_s)

    return [round(start_s - index * step_s, PERIOD_DECIMALS) for index in range(count + 1)]


def summarize_step(
    period_s: float,
    samples: list[ProbeSample],
    *,
    expected_samples: int,
) -> StepSummary:
    """Aggregate one candidate period into the values used for the verdict.

    Warm-up reads are excluded. A step is stable only when all expected reads
    succeeded, no read missed the next scheduled start, and the slowest read
    kept at least 10 % of the period as headroom.

    Args:
        period_s: Candidate period the samples were collected at.
        samples: Samples recorded for this period.
        expected_samples: Number of non-warm-up reads requested for the step.

    Returns:
        Aggregated step statistics including the stability verdict.
    """
    measured = [sample for sample in samples if not sample.warmup]
    successful = [sample for sample in measured if sample.error is None]

    read_times = [sample.read_duration_s for sample in successful if sample.read_duration_s is not None]
    achieved = [sample.achieved_interval_s for sample in successful if sample.achieved_interval_s is not None]
    value_changes = _value_change_intervals(successful)

    read_max = max(read_times) if read_times else None
    stable = (
        len(successful) >= expected_samples
        and read_max is not None
        and read_max <= period_s * STABILITY_READ_HEADROOM
        and not any(sample.deadline_missed for sample in measured)
    )

    return StepSummary(
        period_s=period_s,
        ok=len(successful),
        failed=len(measured) - len(successful),
        warmup=len(samples) - len(measured),
        read_median_s=statistics.median(read_times) if read_times else None,
        read_max_s=read_max,
        achieved_median_s=statistics.median(achieved) if achieved else None,
        deadline_misses=sum(1 for sample in measured if sample.deadline_missed),
        distinct_values=len({sample.value for sample in successful if sample.value is not None}),
        value_change_median_s=statistics.median(value_changes) if value_changes else None,
        stable=stable,
    )


def _value_change_intervals(samples: list[ProbeSample]) -> list[float]:
    """Return elapsed seconds between consecutive distinct register values.

    Uses the measured start offsets, not the scheduled ones, so the estimate
    reflects the meter's real update cadence at this polling period. The first
    observed change has no predecessor and is therefore not counted.
    """
    intervals: list[float] = []
    last_value: float | None = None
    last_change_offset: float | None = None

    for sample in samples:
        if sample.value is None:
            continue

        changed = last_value is not None and sample.value != last_value

        if changed:
            if last_change_offset is not None:
                intervals.append(sample.actual_offset_s - last_change_offset)
            last_change_offset = sample.actual_offset_s

        last_value = sample.value

    return intervals


def _read_once(collector: DlmsCollector) -> tuple[float | None, str | None, float]:
    """Read one power measurement without raising known transport errors.

    Args:
        collector: Connected DLMS collector configured for ``PROBE_OBIS``.

    Returns:
        Tuple of value, error message and read duration in seconds. Non-numeric
        protocol data is not swallowed: it indicates a configuration mismatch
        and should abort the probe with a traceback.
    """
    started = time.monotonic()

    try:
        measurements = collector.collect()
    except (serial.SerialException, TimeoutError, RuntimeError) as exc:
        return None, f"{type(exc).__name__}: {exc}", time.monotonic() - started

    duration = time.monotonic() - started

    if not measurements:
        return None, "collector returned no measurements", duration

    for measurement in measurements:
        if measurement.metric == PROBE_METRIC:
            return measurement.value, None, duration

    return None, f"measurement {PROBE_METRIC!r} missing", duration


def _run_step(
    collector: DlmsCollector,
    period_s: float,
    sample_count: int,
    recorder: CsvRecorder,
) -> list[ProbeSample]:
    """Collect scheduled reads at ``period_s`` until the step is done or fails.

    The step stops at the first failed read: that alone makes the period
    unstable, and reconnecting in the middle would corrupt the start-to-start
    measurements. The caller decides whether to reconnect and continue.

    Args:
        collector: Connected DLMS collector.
        period_s: Candidate start-to-start period.
        sample_count: Number of reads to attempt.
        recorder: CSV sink receiving every sample as it is measured.

    Returns:
        Samples collected for the step, including the failing sample.
    """
    samples: list[ProbeSample] = []
    step_start = time.monotonic()
    previous_offset: float | None = None

    for index in range(sample_count):
        target = step_start + index * period_s
        now = time.monotonic()

        if now < target:
            time.sleep(target - now)

        actual_offset = time.monotonic() - step_start
        value, error, read_duration = _read_once(collector)

        # The read is measured against the next scheduled start, which is what
        # the production loop can actually keep up with.
        deadline_missed = actual_offset + read_duration > (index + 1) * period_s

        sample = ProbeSample(
            period_s=period_s,
            index=index,
            scheduled_offset_s=round(index * period_s, PERIOD_DECIMALS),
            actual_offset_s=actual_offset,
            achieved_interval_s=None if previous_offset is None else actual_offset - previous_offset,
            read_duration_s=read_duration,
            deadline_missed=deadline_missed,
            value=value,
            error=error,
        )
        samples.append(sample)
        recorder.write(sample)
        previous_offset = actual_offset

        if error is not None:
            logger.warning("Read failed at %.2f s period: %s", period_s, error)
            break

    return samples


def _warm_up(collector: DlmsCollector, period_s: float, recorder: CsvRecorder) -> None:
    """Discard initial reads so connection setup does not pollute the ladder.

    IEC baud negotiation, HDLC and AARQ take over a second on a fresh
    connection; including them in the first measured period would make the
    longest period look unstable.

    Args:
        collector: Connected DLMS collector.
        period_s: Period recorded with the warm-up rows.
        recorder: CSV sink receiving the discarded reads.

    Raises:
        ProbeConnectionError: If a warm-up read fails.
    """
    for offset in range(WARMUP_READS, 0, -1):
        value, error, read_duration = _read_once(collector)
        recorder.write(
            ProbeSample(
                period_s=period_s,
                index=-offset,
                scheduled_offset_s=0.0,
                actual_offset_s=0.0,
                achieved_interval_s=None,
                read_duration_s=read_duration,
                deadline_missed=False,
                value=value,
                error=error,
                warmup=True,
            )
        )

        if error is not None:
            raise ProbeConnectionError(f"Warm-up read failed: {error}")


def _reconnect_and_settle(collector: DlmsCollector, period_s: float, recorder: CsvRecorder) -> None:
    """Reconnect once and discard settle reads after a failed step.

    Args:
        collector: Collector whose connection failed.
        period_s: Period recorded with the settle rows.
        recorder: CSV sink receiving the discarded reads.

    Raises:
        ProbeConnectionError: If the connection does not recover.
    """
    logger.info("Reconnecting after failure at %.2f s period", period_s)

    collector.disconnect()
    collector.connect()

    for index in range(SETTLE_SAMPLES):
        value, error, read_duration = _read_once(collector)
        recorder.write(
            ProbeSample(
                period_s=period_s,
                index=-(SETTLE_SAMPLES - index),
                scheduled_offset_s=0.0,
                actual_offset_s=0.0,
                achieved_interval_s=None,
                read_duration_s=read_duration,
                deadline_missed=False,
                value=value,
                error=error,
                warmup=True,
            )
        )

        if error is not None:
            raise ProbeConnectionError(f"Connection did not recover: {error}")


def _measure_period(
    collector: DlmsCollector,
    period_s: float,
    samples_per_step: int,
    recorder: CsvRecorder,
) -> StepSummary:
    """Measure and summarise one candidate period.

    The per-reply timeout is bounded to roughly two periods so an unresponsive
    meter cannot stall the ladder for the 30 s protocol default per register.

    Args:
        collector: Connected and warmed-up DLMS collector.
        period_s: Candidate start-to-start period.
        samples_per_step: Reads to attempt at this period.
        recorder: CSV sink receiving every sample.

    Returns:
        Summary including the stability verdict.
    """
    collector.read_timeout = max(MIN_READ_TIMEOUT_S, period_s * READ_TIMEOUT_FACTOR)

    samples = _run_step(collector, period_s, samples_per_step, recorder)
    summary = summarize_step(period_s, samples, expected_samples=samples_per_step)
    _log_summary(summary)

    return summary


def run_probe(  # noqa: PLR0913 - the ladder knobs are explicit so the fine search stays reproducible.
    *,
    collector: DlmsCollector,
    periods: list[float],
    samples_per_step: int,
    refine_offset_s: float,
    refine_step_s: float,
    floor_s: float,
    recorder: CsvRecorder,
    refine: bool = True,
) -> ProbeResult:
    """Walk the base ladder, optionally refining the area around the first failure.

    The base ladder always stops at the first unstable period. When ``refine``
    is true, the connection is re-established, the fine ladder restarts
    ``refine_offset_s`` above that period (capped at the last known stable
    period) and descends in ``refine_step_s`` increments until it fails again
    or reaches ``floor_s``. When ``refine`` is false, the probe returns
    immediately with the base-ladder result and never reconnects.

    Args:
        collector: Connected and warmed-up DLMS collector.
        periods: Descending coarse and medium periods.
        samples_per_step: Reads per candidate period.
        refine_offset_s: Distance above the failed period where the fine
            ladder starts.
        refine_step_s: Decrement inside the fine ladder.
        floor_s: Shortest period the fine ladder may test.
        recorder: CSV sink receiving every sample.
        refine: Whether to run the optional fine ladder after the first failure.

    Returns:
        Probe result with all step summaries, the fine ladder that was tested
        and the shortest stable period.

    Raises:
        ProbeConnectionError: If the reconnect after the first failure fails.
    """
    summaries: list[StepSummary] = []
    minimum_stable: float | None = None
    first_unstable: float | None = None
    refine_periods: list[float] = []

    _log_summary_header()

    for period_s in periods:
        summary = _measure_period(collector, period_s, samples_per_step, recorder)
        summaries.append(summary)

        if not summary.stable:
            first_unstable = period_s
            break

        minimum_stable = period_s

    if first_unstable is not None and not refine:
        logger.info("Refine disabled; stopping at first unstable period %.2f s", first_unstable)

    if first_unstable is not None and refine:
        refine_periods = build_refine_ladder(
            failure_s=first_unstable,
            offset_s=refine_offset_s,
            step_s=refine_step_s,
            floor_s=floor_s,
            ceiling_s=minimum_stable,
        )

        if refine_periods:
            logger.info(
                "Refining around %.2f s: %d steps from %.2f s to %.2f s in %.2f s steps",
                first_unstable,
                len(refine_periods),
                refine_periods[0],
                refine_periods[-1],
                refine_step_s,
            )

            _reconnect_and_settle(collector, first_unstable, recorder)

            for period_s in refine_periods:
                summary = _measure_period(collector, period_s, samples_per_step, recorder)
                summaries.append(summary)

                if not summary.stable:
                    break

                minimum_stable = period_s

    return ProbeResult(
        periods=periods,
        refine_periods=refine_periods,
        summaries=summaries,
        minimum_stable_s=minimum_stable,
        first_unstable_s=first_unstable,
        csv_path=recorder.path,
    )


def _format_seconds(value: float | None) -> str:
    """Format a duration in seconds as milliseconds for the summary table."""
    return "-" if value is None else f"{value * 1000:.0f}ms"


def _log_summary_header() -> None:
    """Log the fixed-width header of the per-period summary table."""
    logger.info(
        "%8s  %2s  %4s  %8s  %8s  %8s  %6s  %-6s  %8s",
        "period",
        "ok",
        "fail",
        "read_med",
        "read_max",
        "achieved",
        "missed",
        "stable",
        "change",
    )


def _log_summary(summary: StepSummary) -> None:
    """Log one candidate period as a single summary row."""
    logger.info(
        "%7.2fs  %2d  %4d  %8s  %8s  %8s  %6d  %-6s  %8s",
        summary.period_s,
        summary.ok,
        summary.failed,
        _format_seconds(summary.read_median_s),
        _format_seconds(summary.read_max_s),
        _format_seconds(summary.achieved_median_s),
        summary.deadline_misses,
        "yes" if summary.stable else "no",
        _format_seconds(summary.value_change_median_s),
    )


def log_verdict(result: ProbeResult) -> None:
    """Log the two numbers the probe exists to produce.

    Args:
        result: Completed probe result.
    """
    logger.info("-" * 96)

    if result.minimum_stable_s is None:
        logger.error("No stable period found in the tested range")
        return

    logger.info(
        "Minimum stable read period: %.2f s (%.0f ms)",
        result.minimum_stable_s,
        result.minimum_stable_s * 1000,
    )

    # The shortest tested period shows value changes with the best time
    # resolution, so prefer it when estimating the register update cadence.
    value_change = next(
        (summary.value_change_median_s for summary in reversed(result.summaries) if summary.value_change_median_s is not None),
        None,
    )

    if value_change is None:
        logger.info("Estimated value-change period: not observed (register value never changed)")
        return

    logger.info(
        "Estimated value-change period: %.2f s (%.0f ms)",
        value_change,
        value_change * 1000,
    )


def default_output_path(port: str) -> Path:
    """Return a timestamped CSV path for one probe run.

    Args:
        port: Serial device used for the probe.

    Returns:
        Path under ``data/`` that includes the port name and a timestamp.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"dlms_poll_probe_{Path(port).name}_{stamp}.csv"


def _positive_float(value: str) -> float:
    """Parse a strictly positive float for argparse."""
    parsed = float(value)

    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")

    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and validate command-line arguments.

    Args:
        argv: Optional argument list for tests. ``None`` uses ``sys.argv``.

    Returns:
        Validated argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Find the shortest stable DLMS polling period by reading only 16.7.0. "
            "Default ladder: 0.40 s down to 0.05 s in 0.01 s steps, 10 reads per "
            "period. The probe stops at the first unstable period. Use --refine to "
            "reconnect and refine around that boundary."
        )
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help="DLMS serial device")
    parser.add_argument("--start", type=_positive_float, default=DEFAULT_START_PERIOD_S, help="longest coarse period in seconds")
    parser.add_argument("--threshold", type=_positive_float, default=DEFAULT_THRESHOLD_PERIOD_S, help="period at which the medium step starts")
    parser.add_argument("--step-coarse", type=_positive_float, default=DEFAULT_COARSE_STEP_S, help="decrement above the threshold")
    parser.add_argument("--step-medium", type=_positive_float, default=DEFAULT_MEDIUM_STEP_S, help="decrement below the threshold")
    parser.add_argument(
        "--refine-offset",
        type=_positive_float,
        default=DEFAULT_REFINE_OFFSET_S,
        help="distance above the failed period where the fine ladder starts",
    )
    parser.add_argument("--refine-step", type=_positive_float, default=DEFAULT_REFINE_STEP_S, help="decrement inside the fine ladder")
    parser.add_argument("--floor", type=_positive_float, default=DEFAULT_FLOOR_PERIOD_S, help="shortest period tested after a failure")
    parser.add_argument("--samples", type=int, default=MIN_SAMPLES_PER_STEP, help="reads per period (minimum 10)")
    parser.add_argument("--output", help="CSV output path (default: data/dlms_poll_probe_<port>_<timestamp>.csv)")
    parser.add_argument(
        "--refine",
        action="store_true",
        help="reconnect and refine around the first unstable period",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable DEBUG logging (noisy; increases console I/O during timing)",
    )

    args = parser.parse_args(argv)

    if args.samples < MIN_SAMPLES_PER_STEP:
        parser.error(f"--samples must be at least {MIN_SAMPLES_PER_STEP}")

    if args.start <= args.threshold:
        parser.error("--start must be greater than --threshold")

    if args.floor >= args.threshold:
        parser.error("--floor must be below --threshold")

    try:
        build_period_ladder(
            start_s=args.start,
            threshold_s=args.threshold,
            coarse_step_s=args.step_coarse,
            medium_step_s=args.step_medium,
        )
    except ValueError as exc:
        parser.error(str(exc))

    return args


def configure_logging(*, verbose: bool) -> None:
    """Configure console logging for a standalone probe run."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    """Run the probe and return a process exit code.

    Args:
        argv: Optional argument list for tests.

    Returns:
        ``0`` when a stable period was found, ``2`` when none was stable,
        ``3`` when the connection failed, ``130`` when interrupted.
    """
    args = parse_args(argv)
    configure_logging(verbose=args.verbose)

    periods = build_period_ladder(
        start_s=args.start,
        threshold_s=args.threshold,
        coarse_step_s=args.step_coarse,
        medium_step_s=args.step_medium,
    )
    output_path = Path(args.output) if args.output else default_output_path(args.port)

    collector = DlmsCollector(
        enabled=True,
        interval=1,
        source=PROBE_SOURCE,
        port=args.port,
        obis_codes={PROBE_OBIS},
    )
    recorder = CsvRecorder(output_path)

    logger.info("Probing DLMS poll period on %s, register %s only", args.port, PROBE_OBIS)
    logger.info("Base ladder: %d steps from %.2f s down to %.2f s", len(periods), periods[0], periods[-1])
    if args.refine:
        logger.info(
            "Reads per step: %d; on failure reconnect and refine from +%.2f s in %.2f s steps down to %.2f s",
            args.samples,
            args.refine_offset,
            args.refine_step,
            args.floor,
        )
    else:
        logger.info("Reads per step: %d; refine disabled; stop at first unstable period", args.samples)
    logger.info("CSV output: %s", output_path)

    try:
        try:
            collector.connect()
        # TimeoutError is an OSError subclass and is already covered by OSError.
        except (serial.SerialException, OSError, RuntimeError) as exc:
            raise ProbeConnectionError(f"Could not connect to {args.port}: {exc}") from exc

        _warm_up(collector, periods[0], recorder)
        result = run_probe(
            collector=collector,
            periods=periods,
            samples_per_step=args.samples,
            refine_offset_s=args.refine_offset,
            refine_step_s=args.refine_step,
            floor_s=args.floor,
            recorder=recorder,
            refine=args.refine,
        )

    except ProbeConnectionError as exc:
        logger.error("%s", exc)
        return EXIT_CONNECTION_FAILED

    except KeyboardInterrupt:
        logger.warning("Probe interrupted by user")
        return EXIT_INTERRUPTED

    finally:
        recorder.close()
        collector.disconnect()

    log_verdict(result)

    return EXIT_OK if result.minimum_stable_s is not None else EXIT_NO_STABLE_PERIOD


if __name__ == "__main__":
    sys.exit(main())
