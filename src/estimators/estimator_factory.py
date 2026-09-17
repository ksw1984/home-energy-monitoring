from src.config.config import CalculationConfig, EstimatorConfig
from src.estimators.base_estimator import BaseEstimator
from src.estimators.kalman_estimator import KalmanEstimator


def create_estimators(
    configs: list[EstimatorConfig],
    calculations: list[CalculationConfig],
) -> list[BaseEstimator]:
    """Create estimators for calculator input measurements."""
    estimators: list[BaseEstimator] = []

    required_measurements = {
        (input_config.source, input_config.metric)
        for calculation in calculations
        if len({input_config.source for input_config in calculation.inputs.values()}) > 1
        for input_config in calculation.inputs.values()
    }

    for config in configs:
        if not config.enabled:
            continue

        if config.type != "kalman":
            raise ValueError(f"Unknown estimator type: {config.type!r}")

        for source, metric in sorted(required_measurements):
            estimators.append(
                KalmanEstimator(
                    source=source,
                    metric=metric,
                    lookback=config.lookback,
                )
            )

    return estimators
