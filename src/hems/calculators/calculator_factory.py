from src.config.config import CalculatorConfig
from src.hems.calculators.base_calculator import BaseCalculator
from src.hems.calculators.formula_calculator import FormulaCalculator


def create_calculators(
    configs: list[CalculatorConfig],
) -> list[BaseCalculator]:
    """Create calculators from configuration."""
    calculators: list[BaseCalculator] = []

    for config in configs:
        if not config.enabled:
            continue

        if config.type == "formula":
            calculators.append(
                FormulaCalculator(
                    calculations=config.calculations,
                ),
            )
            continue

        raise ValueError(f"Unknown calculator type: {config.type!r}")

    return calculators
