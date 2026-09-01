"""Solve the wood transport challenge instance and report the plan."""

import argparse
from pathlib import Path

import gurobipy as gp

from instance import read_instance
from model import WoodFlowModel
from utils.export import daily_summary, save_solution, solution_to_frame

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "input" / "generic_input_case.xlsx"
DEFAULT_OUTPUT = ROOT / "experiments" / "solution.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wood transport sequencing optimizer.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="input .xlsx file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="solution .csv file")
    parser.add_argument("--days", type=int, default=None, help="truncate the horizon to the first N days")
    parser.add_argument("--time-limit", type=float, default=1200.0, help="solver time limit in seconds")
    return parser.parse_args()


def main() -> None:
    """Read the instance, solve the model and print the resulting plan."""
    args = parse_args()
    instance = read_instance(args.input, max_days=args.days)

    # Feasibility is the hard part of this instance, so bias the search
    # towards finding incumbents early (NoRel heuristic + feasibility focus).
    solver_params = {
        "MIPFocus": 1,
        "Heuristics": 0.5,
        "NoRelHeurTime": args.time_limit * 0.75,
        "Seed": 42,
    }
    try:
        solution = WoodFlowModel(instance).solve(time_limit=args.time_limit, params=solver_params)
    except gp.GurobiError as error:
        raise SystemExit(
            f"Gurobi error: {error}\n"
            "Note: the full instance exceeds the size-limited pip license; "
            "a full Gurobi license is required, or use --days for a smaller run."
        )

    frame = solution_to_frame(solution)
    save_solution(frame, args.output)

    print(f"\nStatus: {solution.status}")
    print(f"Total DB spread over the horizon: {solution.objective:.2f}")
    print("\nDaily summary:")
    print(daily_summary(frame).to_string(index=False))
    print("\nTransport plan:")
    print(frame.to_string(index=False))
    print(f"\nSolution written to {args.output}")


if __name__ == "__main__":
    main()
