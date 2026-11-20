from dataclasses import dataclass


@dataclass(frozen=True)
class OutletTotal:
    outlet: str
    row_count: int
    ml: int


@dataclass(frozen=True)
class CloseoutTotals:
    outlets: tuple[OutletTotal, ...]
    row_count: int
    ml: int


def calculate_totals(groups):
    outlets = tuple(
        OutletTotal(outlet, len(rows), sum(row.ml for row in rows))
        for outlet, rows in groups
    )
    return CloseoutTotals(outlets, sum(item.row_count for item in outlets),
                          sum(item.ml for item in outlets))
