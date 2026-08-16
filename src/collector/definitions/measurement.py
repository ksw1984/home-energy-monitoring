from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Measurement:
    timestamp: datetime
    source: str
    metric: str
    value: float
    unit: str
    obis: str | None = None
