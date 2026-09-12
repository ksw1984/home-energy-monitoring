from src.estimators.kalman_filter import KalmanFilter


def test_initial_value():
    k_filter = KalmanFilter()

    state = k_filter.update(
        value=100,
        dt=1,
    )

    assert state.value == 100
    assert state.slope == 0


def test_rising_signal_estimates_slope():
    k_filter = KalmanFilter(
        process_variance=1.0,
        measurement_variance=0.01,
    )

    k_filter.update(value=0, dt=1)
    k_filter.update(value=10, dt=1)
    state = k_filter.update(value=20, dt=1)

    assert state.slope > 0


def test_variable_dt():
    k_filter = KalmanFilter()

    k_filter.update(value=100, dt=2)
    state = k_filter.update(value=200, dt=5)

    assert state.value > 100
