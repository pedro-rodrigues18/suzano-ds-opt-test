"""Reader and data structures for the wood transport planning instance."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

FRACTION_THRESHOLD = 7000.0
"""Volume (m3) above which a UP may be transported in up to two entries."""


@dataclass(frozen=True)
class ProductionUnit:
    """Harvested production unit (UP) with its wood properties."""

    name: str
    farm: str
    volume: float
    db: float
    rsp: float

    @property
    def is_fractionable(self) -> bool:
        """True if transport may be split into up to two activity intervals."""
        return self.volume > FRACTION_THRESHOLD


@dataclass(frozen=True)
class Transporter:
    """Transport company with its fleet and crane resources."""

    name: str
    fleet_min: int
    fleet_max: int
    cranes: int
    min_vehicle_share: float


@dataclass(frozen=True)
class Route:
    """Feasible (UP, transporter) option with its daily transport rates."""

    up: str
    transporter: str
    factory: str
    load_size: float
    cycle_time: float
    slow_cycle_time: float


@dataclass
class Instance:
    """All input data for one planning horizon."""

    days: list[int]
    day_month: dict[int, int]
    slow_days: set[int]
    ups: dict[str, ProductionUnit]
    transporters: dict[str, Transporter]
    routes: dict[tuple[str, str], Route]
    demand: dict[int, tuple[float, float]]
    rsp_limits: dict[int, tuple[float, float]]

    @property
    def farms(self) -> dict[str, list[str]]:
        """Mapping of farm name to the names of its UPs."""
        farms: dict[str, list[str]] = {}
        for up in self.ups.values():
            farms.setdefault(up.farm, []).append(up.name)
        return farms

    def capacity(self, up: str, transporter: str, day: int) -> float:
        """Daily volume (m3) that one vehicle can move on the route.

        A slow-cycle rate of zero means the route is not affected by
        slow-cycle days, so the regular cycle time is used.
        """
        route = self.routes[up, transporter]
        cycle = route.cycle_time
        if day in self.slow_days and route.slow_cycle_time > 0:
            cycle = route.slow_cycle_time
        return route.load_size * cycle


def read_instance(path: str | Path, max_days: int | None = None) -> Instance:
    """Load an instance from the challenge .xlsx input file.

    Args:
        path: Workbook with sheets HORIZONTE, BD_UP, FROTA, GRUA,
            FABRICA and ROTA.
        max_days: Optional horizon truncation, useful for quick experiments.

    Assumptions documented in the report: fleet bounds with an empty DIA
    column apply to every day, and a single factory is considered.
    """
    sheets = pd.read_excel(path, sheet_name=None)

    horizon = sheets["HORIZONTE"]
    if max_days is not None:
        horizon = horizon.head(max_days)
    days = [int(day) for day in horizon["DIA"]]
    day_month = {int(row.DIA): int(row.MES) for row in horizon.itertuples()}
    slow_days = {int(row.DIA) for row in horizon.itertuples() if pd.notna(row.CICLO_LENTO)}

    ups = {
        row.UP: ProductionUnit(
            name=row.UP,
            farm=row.FAZENDA,
            volume=float(row.VOLUME),
            db=float(row.DB),
            rsp=float(row.RSP),
        )
        for row in sheets["BD_UP"].itertuples()
    }

    fleet = sheets["FROTA"].set_index("TRANSPORTADOR")
    cranes = sheets["GRUA"].set_index("TRANSPORTADOR")
    transporters = {
        str(name): Transporter(
            name=str(name),
            fleet_min=int(fleet.loc[name, "FROTA_MIN"]),
            fleet_max=int(fleet.loc[name, "FROTA_MAX"]),
            cranes=int(cranes.loc[name, "QTD_GRUAS"]),
            min_vehicle_share=float(cranes.loc[name, "PORCENTAGEM_VEICULOS_MIN"]),
        )
        for name in fleet.index
    }

    routes = {
        (row.ORIGEM, row.TRANSPORTADOR): Route(
            up=row.ORIGEM,
            transporter=row.TRANSPORTADOR,
            factory=row.DESTINO,
            load_size=float(row.CAIXA_CARGA),
            cycle_time=float(row.TEMPO_CICLO),
            slow_cycle_time=float(row.CICLO_LENTO),
        )
        for row in sheets["ROTA"].itertuples()
    }

    factory = sheets["FABRICA"]
    factory = factory[factory["DIA"].isin(days)]
    demand = {int(row.DIA): (float(row.DEMANDA_MIN), float(row.DEMANDA_MAX)) for row in factory.itertuples()}
    rsp_limits = {int(row.DIA): (float(row.RSP_MIN), float(row.RSP_MAX)) for row in factory.itertuples()}

    return Instance(
        days=days,
        day_month=day_month,
        slow_days=slow_days,
        ups=ups,
        transporters=transporters,
        routes=routes,
        demand=demand,
        rsp_limits=rsp_limits,
    )
