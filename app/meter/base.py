from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Reading:
    ts: float                          # UTC epoch seconds
    import_kwh: float | None = None    # cumulative counter, kWh
    export_kwh: float | None = None
    power_w: float | None = None       # instantaneous import, W
    export_power_w: float | None = None
    extra: dict = field(default_factory=dict)
