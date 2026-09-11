from src.config.config import EstimatorConfig
from src.estimators.base_estimator import BaseEstimator
from src.estimators.kalman_estimator import KalmanEstimator


def create_estimators(
    configs: list[EstimatorConfig],
) -> list[BaseEstimator]:
    """Create estimators from configuration."""
    estimators: list[BaseEstimator] = []

    for config in configs:
        if not config.enabled:
            continue

        for measurement in config.measurements:
            if config.type == "kalman":
                estimators.append(
                    KalmanEstimator(
                        source=measurement.source,
                        metric=measurement.metric,
                        latency=config.latency,
                        history_size=config.history_size,
                    )
                )
                continue

            raise ValueError(f"Unknown estimator type: {config.type!r}")

    return estimators
