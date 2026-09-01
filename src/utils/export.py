"""Helpers to convert and export solutions in the challenge output format."""

from pathlib import Path

import pandas as pd

from model import Solution

OUTPUT_COLUMNS = ["UP", "FAZENDA", "TRANSPORTADOR", "DIA", "MES", "DB", "RSP", "QTD_VEICULOS", "VOLUME"]


def solution_to_frame(solution: Solution) -> pd.DataFrame:
    """Return the solution rows as a DataFrame in the expected output layout."""
    records = [
        {
            "UP": row.up,
            "FAZENDA": row.farm,
            "TRANSPORTADOR": row.transporter,
            "DIA": row.day,
            "MES": row.month,
            "DB": row.db,
            "RSP": row.rsp,
            "QTD_VEICULOS": row.vehicles,
            "VOLUME": row.volume,
        }
        for row in solution.rows
    ]
    return pd.DataFrame(records, columns=OUTPUT_COLUMNS)


def daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-day totals: volume, DB spread and volume-weighted RSP."""
    rows = []
    for day, group in frame.groupby("DIA"):
        volume = group["VOLUME"].sum()
        rows.append(
            {
                "DIA": day,
                "VOLUME": round(volume, 2),
                "DB_SPREAD": round(group["DB"].max() - group["DB"].min(), 2),
                "RSP_MEAN": round((group["RSP"] * group["VOLUME"]).sum() / volume, 4),
                "UPS_ATIVAS": group["UP"].nunique(),
            }
        )
    return pd.DataFrame(rows)


def save_solution(frame: pd.DataFrame, path: str | Path) -> None:
    """Write the solution to CSV, creating parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
