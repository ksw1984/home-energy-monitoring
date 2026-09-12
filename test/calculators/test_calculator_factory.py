from src.calculators.calculator_factory import create_calculators
from src.calculators.formula_calculator import FormulaCalculator
from src.config.config import (
    CalculationConfig,
    CalculatorConfig,
    CalculatorInputConfig,
)


def test_create_formula_calculator():
    calculation = CalculationConfig(
        source="calculated",
        metric="heat_pump_power",
        unit="W",
        formula="grid - household",
        inputs={
            "grid": CalculatorInputConfig(
                source="meter_grid",
                metric="grid_import_power",
            ),
            "household": CalculatorInputConfig(
                source="meter_household",
                metric="grid_import_power",
            ),
        },
    )

    config = CalculatorConfig(
        type="formula",
        enabled=True,
        calculations=[calculation],
    )

    calculators = create_calculators([config])

    assert len(calculators) == 1
    assert isinstance(calculators[0], FormulaCalculator)
    assert calculators[0].calculations == [calculation]
