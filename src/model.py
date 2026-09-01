"""MILP for the daily wood transport sequencing problem, solved with Gurobi."""

from collections import defaultdict
from dataclasses import dataclass

import gurobipy as gp
from gurobipy import GRB

from instance import Instance

STATUS_NAMES = {
    GRB.OPTIMAL: "optimal",
    GRB.TIME_LIMIT: "time_limit",
    GRB.INFEASIBLE: "infeasible",
    GRB.INTERRUPTED: "interrupted",
}


@dataclass(frozen=True)
class SolutionRow:
    """Transport decision for one (UP, transporter, day)."""

    up: str
    farm: str
    transporter: str
    day: int
    month: int
    db: float
    rsp: float
    vehicles: int
    volume: float


@dataclass
class Solution:
    """Result of one optimization run."""

    status: str
    objective: float
    rows: list[SolutionRow]


class WoodFlowModel:
    """Wood transport MILP.

    Minimizes the sum over the horizon of the daily basic-density (DB)
    spread of the wood delivered to the factory, subject to demand,
    RSP quality, fleet, crane and campaign (contiguity) rules.
    """

    def __init__(self, instance: Instance):
        self.inst = instance
        self.model = gp.Model("wood-transport")
        self._build_index_sets()
        self._add_variables()
        self._add_flow_constraints()
        self._add_factory_constraints()
        self._add_resource_constraints()
        self._add_campaign_constraints()
        self._set_objective()

    def _build_index_sets(self) -> None:
        inst = self.inst
        self.days = list(inst.days)
        self.prev_day = {day: (self.days[i - 1] if i > 0 else None) for i, day in enumerate(self.days)}
        self.farm_ups = inst.farms
        self.farm_of = {name: up.farm for name, up in inst.ups.items()}
        self.big_ups = [name for name, up in inst.ups.items() if up.is_fractionable]
        self.max_db = max(up.db for up in inst.ups.values())

        self.ups_of_carrier: dict[str, list[str]] = defaultdict(list)
        self.carriers_of_up: dict[str, list[str]] = defaultdict(list)
        for up, carrier in inst.routes:
            self.ups_of_carrier[carrier].append(up)
            self.carriers_of_up[up].append(carrier)
        self.farm_carrier_pairs = sorted({(self.farm_of[up], carrier) for up, carrier in inst.routes})
        self.farms_of_carrier: dict[str, list[str]] = defaultdict(list)
        for farm, carrier in self.farm_carrier_pairs:
            self.farms_of_carrier[carrier].append(farm)

    def _add_variables(self) -> None:
        m, inst = self.model, self.inst
        route_days = [(u, k, d) for (u, k) in inst.routes for d in self.days]
        farm_days = [(f, k, d) for (f, k) in self.farm_carrier_pairs for d in self.days]
        up_days = [(u, d) for u in inst.ups for d in self.days]

        self.volume = m.addVars(route_days, name="volume")
        self.vehicles = m.addVars(route_days, vtype=GRB.INTEGER, name="vehicles")
        self.front_open = m.addVars(route_days, vtype=GRB.BINARY, name="front_open")
        self.at_farm = m.addVars(farm_days, vtype=GRB.BINARY, name="at_farm")
        self.up_active = m.addVars(up_days, vtype=GRB.BINARY, name="up_active")
        self.up_done = m.addVars(up_days, vtype=GRB.BINARY, name="up_done")
        self.farm_done = m.addVars(
            [(f, d) for f in self.farm_ups for d in self.days], vtype=GRB.BINARY, name="farm_done"
        )
        self.entry = m.addVars([(u, d) for u in self.big_ups for d in self.days], vtype=GRB.BINARY, name="entry")
        self.db_max = m.addVars(self.days, name="db_max")
        self.db_min = m.addVars(self.days, ub=self.max_db, name="db_min")

    def _add_flow_constraints(self) -> None:
        """Vehicle capacity, activity linking and UP volume availability."""
        m, inst = self.model, self.inst
        for (u, k), route in inst.routes.items():
            fleet_max = inst.transporters[k].fleet_max
            for d in self.days:
                m.addConstr(self.volume[u, k, d] <= inst.capacity(u, k, d) * self.vehicles[u, k, d])
                m.addConstr(self.vehicles[u, k, d] <= fleet_max * self.front_open[u, k, d])
                m.addConstr(self.vehicles[u, k, d] >= self.front_open[u, k, d])
                # An open front must move at least one full load.
                m.addConstr(self.volume[u, k, d] >= route.load_size * self.front_open[u, k, d])
        for u, up in inst.ups.items():
            m.addConstr(self.volume.sum(u, "*", "*") <= up.volume)

    def _add_factory_constraints(self) -> None:
        """Daily demand window and volume-weighted RSP quality window."""
        m, inst = self.model, self.inst
        for d in self.days:
            total = self.volume.sum("*", "*", d)
            demand_min, demand_max = inst.demand[d]
            m.addConstr(total >= demand_min)
            m.addConstr(total <= demand_max)

            rsp_min, rsp_max = inst.rsp_limits[d]
            weighted = gp.quicksum(inst.ups[u].rsp * self.volume[u, k, d] for (u, k) in inst.routes)
            m.addConstr(weighted >= rsp_min * total)
            m.addConstr(weighted <= rsp_max * total)

    def _add_resource_constraints(self) -> None:
        """Single farm per day, fleet bounds, cranes and vehicle share."""
        m, inst = self.model, self.inst
        for k, transporter in inst.transporters.items():
            for d in self.days:
                fronts = self.ups_of_carrier[k]
                total_vehicles = gp.quicksum(self.vehicles[u, k, d] for u in fronts)
                active = gp.quicksum(self.at_farm[f, k, d] for f in self.farms_of_carrier[k])

                m.addConstr(active <= 1)
                m.addConstr(total_vehicles >= transporter.fleet_min * active)
                m.addConstr(total_vehicles <= transporter.fleet_max * active)
                m.addConstr(gp.quicksum(self.front_open[u, k, d] for u in fronts) <= transporter.cranes)

                for u in fronts:
                    m.addConstr(self.front_open[u, k, d] <= self.at_farm[self.farm_of[u], k, d])
                    # Each active front holds a minimum share of the working fleet.
                    m.addConstr(
                        self.vehicles[u, k, d]
                        >= transporter.min_vehicle_share * total_vehicles
                        - transporter.fleet_max * (1 - self.front_open[u, k, d])
                    )

    def _add_campaign_constraints(self) -> None:
        """Completion tracking and contiguity (campaign) rules."""
        m, inst = self.model, self.inst

        # Completion flags can only be raised once all volume was moved.
        for u, up in inst.ups.items():
            cumulative = gp.LinExpr()
            for d in self.days:
                cumulative = cumulative + self.volume.sum(u, "*", d)
                m.addConstr(cumulative >= up.volume * self.up_done[u, d])
                prev = self.prev_day[d]
                if prev is not None:
                    m.addConstr(self.up_done[u, d] >= self.up_done[u, prev])

        for farm, up_names in self.farm_ups.items():
            farm_volume = sum(inst.ups[u].volume for u in up_names)
            cumulative = gp.LinExpr()
            for d in self.days:
                cumulative = cumulative + gp.quicksum(self.volume.sum(u, "*", d) for u in up_names)
                m.addConstr(cumulative >= farm_volume * self.farm_done[farm, d])
                prev = self.prev_day[d]
                if prev is not None:
                    m.addConstr(self.farm_done[farm, d] >= self.farm_done[farm, prev])

        # A carrier that starts a farm stays there until the farm is finished.
        for farm, k in self.farm_carrier_pairs:
            for d in self.days:
                prev = self.prev_day[d]
                if prev is not None:
                    m.addConstr(self.at_farm[farm, k, prev] <= self.at_farm[farm, k, d] + self.farm_done[farm, prev])

        # A front opened at a small UP stays open until the UP is finished.
        for (u, k) in inst.routes:
            if inst.ups[u].is_fractionable:
                continue
            for d in self.days:
                prev = self.prev_day[d]
                if prev is not None:
                    m.addConstr(self.front_open[u, k, prev] <= self.front_open[u, k, d] + self.up_done[u, prev])

        # Daily UP activity aggregated over carriers.
        for u in inst.ups:
            for d in self.days:
                for k in self.carriers_of_up[u]:
                    m.addConstr(self.up_active[u, d] >= self.front_open[u, k, d])
                m.addConstr(self.up_active[u, d] <= self.front_open.sum(u, "*", d))

        # Large UPs allow at most two disjoint activity intervals.
        for u in self.big_ups:
            for d in self.days:
                prev = self.prev_day[d]
                previous_activity = self.up_active[u, prev] if prev is not None else 0
                m.addConstr(self.entry[u, d] >= self.up_active[u, d] - previous_activity)
            m.addConstr(self.entry.sum(u, "*") <= 2)

    def _set_objective(self) -> None:
        """Minimize the sum of daily DB ranges over active UPs."""
        m, inst = self.model, self.inst
        for u, up in inst.ups.items():
            for d in self.days:
                m.addConstr(self.db_max[d] >= up.db * self.up_active[u, d])
                m.addConstr(self.db_min[d] <= up.db * self.up_active[u, d] + self.max_db * (1 - self.up_active[u, d]))
        for d in self.days:
            m.addConstr(self.db_min[d] <= self.db_max[d])
        m.setObjective(gp.quicksum(self.db_max[d] - self.db_min[d] for d in self.days), GRB.MINIMIZE)

    def solve(
        self,
        time_limit: float | None = None,
        mip_gap: float | None = None,
        verbose: bool = True,
        params: dict[str, float] | None = None,
    ) -> Solution:
        """Optimize the model and return the transport plan.

        Args:
            params: Extra Gurobi parameters, e.g. {"NoRelHeurTime": 60}.

        Raises:
            RuntimeError: If no feasible solution is available.
        """
        m = self.model
        m.Params.OutputFlag = 1 if verbose else 0
        if time_limit is not None:
            m.Params.TimeLimit = time_limit
        if mip_gap is not None:
            m.Params.MIPGap = mip_gap
        for name, value in (params or {}).items():
            m.setParam(name, value)
        m.optimize()

        status = STATUS_NAMES.get(m.Status, f"status_{m.Status}")
        if m.SolCount == 0:
            raise RuntimeError(f"No feasible solution found ({status}).")
        return Solution(status=status, objective=m.ObjVal, rows=self._extract_rows())

    def _extract_rows(self) -> list[SolutionRow]:
        inst = self.inst
        rows = []
        for (u, k, d), front in self.front_open.items():
            if front.X < 0.5:
                continue
            up = inst.ups[u]
            rows.append(
                SolutionRow(
                    up=u,
                    farm=up.farm,
                    transporter=k,
                    day=d,
                    month=inst.day_month[d],
                    db=up.db,
                    rsp=up.rsp,
                    vehicles=round(self.vehicles[u, k, d].X),
                    volume=round(self.volume[u, k, d].X, 4),
                )
            )
        rows.sort(key=lambda row: (row.day, row.transporter, row.up))
        return rows
