from collections import defaultdict


def group_by_outlet(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row.outlet].append(row)
    return tuple((outlet, tuple(groups[outlet])) for outlet in sorted(groups))
