from itertools import pairwise
from unittest.mock import Mock

from src.scripts.check_dlms_poll_interval import (
    _run_step,
    build_period_ladder,
    build_refine_ladder,
    PROBE_METRIC,
    ProbeSample,
    run_probe,
    summarize_step,
)

import pytest


def make_sample(  # noqa: PLR0913 - a sample mirror maps every CSV column.
    index: int,
    *,
    period_s: float = 1.0,
    actual_offset_s: float | None = None,
    achieved_interval_s: float | None = None,
    read_duration_s: float = 0.1,
    deadline_missed: bool = False,
    value: float | None = 1000.0,
    error: str | None = None,
    warmup: bool = False,
) -> ProbeSample:
    offset = index * period_s if actual_offset_s is None else actual_offset_s
    return ProbeSample(
        period_s=period_s,
        index=index,
        scheduled_offset_s=index * period_s,
        actual_offset_s=offset,
        achieved_interval_s=achieved_interval_s,
        read_duration_s=read_duration_s,
        deadline_missed=deadline_missed,
        value=value,
        error=error,
        warmup=warmup,
    )


def make_step(sample_count: int = 10, **kwargs) -> list[ProbeSample]:
    return [make_sample(index, achieved_interval_s=None if index == 0 else 1.0, **kwargs) for index in range(sample_count)]


# ---------------------------------------------------------
# Period ladder
# ---------------------------------------------------------


def test_build_period_ladder_default_matches_documented_steps():
    ladder = build_period_ladder()

    assert ladder[0] == pytest.approx(5.0)
    assert ladder[-1] == pytest.approx(0.1)
    assert 1.0 in ladder
    assert 0.9 in ladder
    assert len(ladder) == 18
    assert all(left > right for left, right in pairwise(ladder))


def test_build_period_ladder_uses_coarse_then_medium_step():
    ladder = build_period_ladder(
        start_s=1.2,
        threshold_s=1.0,
        coarse_step_s=0.1,
        medium_step_s=0.05,
        base_end_s=0.9,
    )

    assert ladder == pytest.approx([1.2, 1.1, 1.0, 0.95, 0.9])


def test_build_period_ladder_custom_range():
    ladder = build_period_ladder(
        start_s=2.0,
        threshold_s=0.5,
        coarse_step_s=0.5,
        medium_step_s=0.05,
        base_end_s=0.3,
    )

    assert ladder == pytest.approx([2.0, 1.5, 1.0, 0.5, 0.45, 0.4, 0.35, 0.3])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"start_s": 1.0, "threshold_s": 1.0}, "start_s must be greater"),
        ({"base_end_s": 0.0}, "base_end_s must be positive"),
        ({"base_end_s": 2.0}, "base_end_s must be positive"),
        ({"coarse_step_s": 0.0}, "steps must be positive"),
        ({"medium_step_s": -1.0}, "steps must be positive"),
        ({"medium_step_s": 0.95}, "medium_step_s is too large"),
    ],
)
def test_build_period_ladder_rejects_invalid_ranges(kwargs, message):
    with pytest.raises(ValueError, match=message):
        build_period_ladder(**kwargs)


# ---------------------------------------------------------
# Refinement ladder
# ---------------------------------------------------------


def test_build_refine_ladder_starts_above_failure_and_reaches_floor():
    ladder = build_refine_ladder(failure_s=0.4)

    assert ladder[0] == pytest.approx(0.6)
    assert ladder[-1] == pytest.approx(0.05)
    assert len(ladder) == 56
    assert all(left - right == pytest.approx(0.01) for left, right in pairwise(ladder))


def test_build_refine_ladder_is_capped_at_last_stable_period():
    ladder = build_refine_ladder(failure_s=0.4, ceiling_s=0.5)

    assert ladder[0] == pytest.approx(0.5)
    assert ladder[-1] == pytest.approx(0.05)


def test_build_refine_ladder_honours_custom_floor():
    ladder = build_refine_ladder(failure_s=0.06, floor_s=0.1)

    assert ladder[0] == pytest.approx(0.26)
    assert ladder[-1] == pytest.approx(0.1)


def test_build_refine_ladder_returns_empty_when_start_is_below_floor():
    assert build_refine_ladder(failure_s=0.4, ceiling_s=0.02, floor_s=0.05) == []


@pytest.mark.parametrize("kwargs", [{"offset_s": 0.0}, {"step_s": 0.0}, {"floor_s": 0.0}])
def test_build_refine_ladder_rejects_invalid_steps(kwargs):
    with pytest.raises(ValueError, match="must be positive"):
        build_refine_ladder(failure_s=0.4, **kwargs)


# ---------------------------------------------------------
# Step summaries
# ---------------------------------------------------------


def test_summarize_step_reports_stable_step_with_read_and_value_change_stats():
    samples = [make_sample(index, actual_offset_s=float(index), value=float(index // 2)) for index in range(10)]

    summary = summarize_step(1.0, samples, expected_samples=10)

    assert summary.stable is True
    assert summary.period_s == 1.0
    assert summary.ok == 10
    assert summary.failed == 0
    assert summary.warmup == 0
    assert summary.read_median_s == pytest.approx(0.1)
    assert summary.read_max_s == pytest.approx(0.1)
    assert summary.deadline_misses == 0
    assert summary.distinct_values == 5
    # Values change every two samples, i.e. every 2 s at a 1 s period.
    assert summary.value_change_median_s == pytest.approx(2.0)


def test_summarize_step_marks_slow_read_unstable():
    samples = make_step(read_duration_s=0.95)

    summary = summarize_step(1.0, samples, expected_samples=10)

    assert summary.stable is False
    assert summary.read_max_s == pytest.approx(0.95)


def test_summarize_step_marks_deadline_miss_unstable():
    samples = make_step()
    samples[3] = make_sample(3, deadline_missed=True)

    summary = summarize_step(1.0, samples, expected_samples=10)

    assert summary.stable is False
    assert summary.deadline_misses == 1


def test_summarize_step_requires_all_expected_reads():
    summary = summarize_step(1.0, make_step(sample_count=9), expected_samples=10)

    assert summary.stable is False
    assert summary.ok == 9


def test_summarize_step_excludes_warmup_reads():
    samples = make_step()
    samples.append(make_sample(-1, warmup=True, error="connection settling", read_duration_s=None))

    summary = summarize_step(1.0, samples, expected_samples=10)

    assert summary.stable is True
    assert summary.warmup == 1
    assert summary.failed == 0


def test_summarize_step_counts_failures_and_omits_read_stats_when_all_failed():
    samples = [make_sample(index, error="TimeoutError: boom", read_duration_s=None) for index in range(10)]

    summary = summarize_step(1.0, samples, expected_samples=10)

    assert summary.stable is False
    assert summary.ok == 0
    assert summary.failed == 10
    assert summary.read_median_s is None
    assert summary.read_max_s is None
    assert summary.achieved_median_s is None


def test_summarize_step_reports_no_value_change_for_constant_register():
    summary = summarize_step(1.0, make_step(value=42.0), expected_samples=10)

    assert summary.distinct_values == 1
    assert summary.value_change_median_s is None


# ---------------------------------------------------------
# Step execution and ladder loop
# ---------------------------------------------------------


def test_run_step_stops_after_failed_read(monkeypatch):
    module = "src.scripts.check_dlms_poll_interval"
    results = iter(
        [
            (1.0, None, 0.01),
            (None, "TimeoutError: boom", 0.01),
            (1.0, None, 0.01),
        ]
    )
    monkeypatch.setattr(f"{module}._read_once", lambda _collector: next(results))

    recorder = Mock()
    samples = _run_step(Mock(), 0.01, 5, recorder)

    assert len(samples) == 2
    assert samples[1].error == "TimeoutError: boom"
    assert recorder.write.call_count == 2


def test_run_probe_refines_failure_area(monkeypatch):
    module = "src.scripts.check_dlms_poll_interval"

    def fake_run_step(_collector, period_s, _samples, _recorder):
        return make_step(read_duration_s=0.1) if period_s >= 0.49 else make_step(read_duration_s=0.95)

    monkeypatch.setattr(f"{module}._run_step", fake_run_step)
    reconnect = Mock()
    monkeypatch.setattr(f"{module}._reconnect_and_settle", reconnect)

    result = run_probe(
        collector=Mock(),
        periods=[1.0, 0.5, 0.4],
        samples_per_step=10,
        refine_offset_s=0.2,
        refine_step_s=0.01,
        floor_s=0.05,
        recorder=Mock(),
    )

    # 1.0 and 0.5 are stable, 0.4 fails. The fine ladder is capped at the last
    # stable period 0.5 and stops at the first failure, 0.48.
    assert result.first_unstable_s == 0.4
    assert result.refine_periods[0] == pytest.approx(0.5)
    assert result.refine_periods[1] == pytest.approx(0.49)
    assert result.minimum_stable_s == pytest.approx(0.49)
    assert len(result.summaries) == 6
    reconnect.assert_called_once()


def test_run_probe_without_failure_does_not_refine(monkeypatch):
    module = "src.scripts.check_dlms_poll_interval"
    monkeypatch.setattr(f"{module}._run_step", lambda *_args: make_step(read_duration_s=0.1))
    reconnect = Mock()
    monkeypatch.setattr(f"{module}._reconnect_and_settle", reconnect)

    result = run_probe(
        collector=Mock(),
        periods=[1.0, 0.5],
        samples_per_step=10,
        refine_offset_s=0.2,
        refine_step_s=0.01,
        floor_s=0.05,
        recorder=Mock(),
    )

    assert result.minimum_stable_s == 0.5
    assert result.first_unstable_s is None
    assert result.refine_periods == []
    assert len(result.summaries) == 2
    reconnect.assert_not_called()


def test_probe_metric_is_the_power_register_metric():
    assert PROBE_METRIC == "grid_active_power"
