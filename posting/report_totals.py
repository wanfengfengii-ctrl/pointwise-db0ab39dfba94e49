from dataclasses import dataclass


@dataclass(frozen=True)
class ReportTotals:
    outlets: tuple[tuple[str, int, int], ...]
    row_count: int
    ml: int


def summarize_rows(rows):
    per_outlet = {}
    total_count = 0
    total_ml = 0
    for row in rows:
        count, ml = per_outlet.get(row.outlet, (0, 0))
        per_outlet[row.outlet] = count + 1, ml + row.ml
        total_count += 1
        total_ml += row.ml
    outlets = tuple((outlet, count, ml) for outlet, (count, ml) in sorted(per_outlet.items()))
    return ReportTotals(outlets, total_count, total_ml)
