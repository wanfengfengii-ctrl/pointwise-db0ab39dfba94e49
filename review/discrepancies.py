from dataclasses import dataclass


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    key: int | tuple[str, ...]
    expected: object
    actual: object


def compare_rows(original, ledger):
    expected = {row.ordinal: (row.outlet, row.ml) for row in original}
    actual = {row.ordinal: (row.outlet, row.ml) for row in ledger}
    differences = []
    for ordinal in sorted(expected.keys() | actual.keys()):
        before = expected.get(ordinal)
        after = actual.get(ordinal)
        if before == after:
            continue
        if before is None:
            kind = 'extra_row'
        elif after is None:
            kind = 'missing_row'
        else:
            kind = 'row_mismatch'
        differences.append(Discrepancy(kind, ordinal, before, after))
    return tuple(differences)


def compare_totals(expected, actual, scope):
    differences = []
    for field in ('row_count', 'ml'):
        before = getattr(expected, field)
        after = getattr(actual, field)
        if before != after:
            differences.append(Discrepancy('total_mismatch', (scope, 'batch', field), before, after))
    before_by_outlet = {outlet: (count, ml) for outlet, count, ml in expected.outlets}
    after_by_outlet = {outlet: (count, ml) for outlet, count, ml in actual.outlets}
    for outlet in sorted(before_by_outlet.keys() | after_by_outlet.keys()):
        before = before_by_outlet.get(outlet)
        after = after_by_outlet.get(outlet)
        key = (scope, 'outlet', outlet)
        if before is None or after is None:
            differences.append(Discrepancy('total_mismatch', key, before, after))
            continue
        for field, expected_value, actual_value in zip(('row_count', 'ml'), before, after):
            if expected_value != actual_value:
                differences.append(Discrepancy('total_mismatch', (*key, field), expected_value, actual_value))
    return tuple(differences)
