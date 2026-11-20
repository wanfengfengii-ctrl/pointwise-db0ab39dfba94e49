import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReportRow:
    ordinal: int
    outlet: str
    ml: int


@dataclass(frozen=True)
class DecodedReport:
    report_id: str
    result_id: int
    plant: str
    day: str
    batch: str
    revision_id: int
    rows: tuple[ReportRow, ...]
    declared_outlets: tuple[tuple[str, int, int], ...]
    declared_row_count: int
    declared_ml: int


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate field {key}')
        result[key] = value
    return result


def _fields(value, keys, field):
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f'invalid {field} fields')


def _text(value, field):
    if type(value) is not str or not value.strip():
        raise ValueError(f'{field} must be nonblank text')
    value.encode('utf-8')
    return value


def _integer(value, field, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f'invalid {field} integer')
    return value


def _ml(value):
    if type(value) is not str or re.fullmatch(r'0|-?[1-9][0-9]*', value) is None:
        raise ValueError('ml must be canonical decimal text')
    return int(Decimal(value))


def _rows(sequence):
    if type(sequence) is not list:
        raise ValueError('rows must be an array')
    rows = []
    for ordinal, item in enumerate(sequence, start=1):
        _fields(item, {'ordinal', 'outlet', 'ml'}, 'measurement row')
        if _integer(item['ordinal'], 'ordinal', 1) != ordinal:
            raise ValueError('measurement ordinals must follow input order')
        rows.append(ReportRow(ordinal, _text(item['outlet'], 'outlet'), _ml(item['ml'])))
    return tuple(rows)


def _outlet_claims(sequence):
    if type(sequence) is not list:
        raise ValueError('outlets must be an array')
    claims = []
    for item in sequence:
        _fields(item, {'outlet', 'row_count', 'ml'}, 'outlet total')
        outlet = _text(item['outlet'], 'outlet')
        if claims and outlet <= claims[-1][0]:
            raise ValueError('declared outlets must be unique and sorted')
        claims.append((outlet, _integer(item['row_count'], 'row_count', 1), _ml(item['ml'])))
    return tuple(claims)


@dataclass(frozen=True)
class SlotEvidence:
    ordinal: int
    ml: int
    outlets: tuple[str, ...]
    allocation: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class DecodedSharedReport:
    report_id: str
    result_id: int
    plant: str
    day: str
    batch: str
    revision_id: int
    slots: tuple[SlotEvidence, ...]
    bounds: tuple[tuple[str, int, int], ...]
    rows: tuple[ReportRow, ...]
    declared_outlets: tuple[tuple[str, int, int], ...]
    declared_row_count: int
    declared_ml: int


def _canonical_ml(value, field):
    try:
        return _ml(value)
    except ValueError as error:
        raise ValueError(f'{field} {error}') from error


def _shared_slots(sequence):
    if type(sequence) is not list:
        raise ValueError('slots must be an array')
    slots = []
    for position, item in enumerate(sequence, start=1):
        _fields(item, {'ordinal', 'ml', 'outlets'}, 'shared slot')
        if _integer(item['ordinal'], 'slot ordinal', 1) != position:
            raise ValueError('slot ordinals must follow input order')
        ml = _canonical_ml(item['ml'], 'slot ml')
        if ml < 0:
            raise ValueError('slot ml must be non-negative')
        permits = item['outlets']
        if type(permits) is not list:
            raise ValueError('slot outlets must be an array')
        names = []
        for raw in permits:
            name = _text(raw, 'slot outlet')
            if names and name <= names[-1]:
                raise ValueError('slot outlets must be unique and sorted')
            names.append(name)
        slots.append((position, ml, tuple(names)))
    return tuple(slots)


def _shared_bounds(sequence):
    if type(sequence) is not list:
        raise ValueError('bounds must be an array')
    bounds = []
    for item in sequence:
        _fields(item, {'outlet', 'min_ml', 'max_ml'}, 'outlet bound')
        outlet = _text(item['outlet'], 'bound outlet')
        if bounds and outlet <= bounds[-1][0]:
            raise ValueError('bounds must be unique and sorted')
        low = _canonical_ml(item['min_ml'], 'bound min_ml')
        high = _canonical_ml(item['max_ml'], 'bound max_ml')
        if low < 0 or high < low:
            raise ValueError('bound must satisfy 0 <= min_ml <= max_ml')
        bounds.append((outlet, low, high))
    return tuple(bounds)


def _shared_allocations(sequence, slots):
    if type(sequence) is not list:
        raise ValueError('allocations must be an array')
    permits = {ordinal: set(outlets) for ordinal, _, outlets in slots}
    allocations = []
    previous = None
    for item in sequence:
        _fields(item, {'ordinal', 'outlet', 'ml'}, 'slot allocation')
        ordinal = _integer(item['ordinal'], 'allocation ordinal', 1)
        if ordinal not in permits:
            raise ValueError('allocation references an unknown slot')
        outlet = _text(item['outlet'], 'allocation outlet')
        if outlet not in permits[ordinal]:
            raise ValueError('allocation uses an outlet the slot does not permit')
        ml = _canonical_ml(item['ml'], 'allocation ml')
        if ml <= 0:
            raise ValueError('allocation ml must be positive')
        key = (ordinal, outlet)
        if previous is not None and key <= previous:
            raise ValueError('allocations must be sorted by slot then outlet')
        previous = key
        allocations.append(key + (ml,))
    return tuple(allocations)


def decode_shared_report(envelope):
    report_id = _text(getattr(envelope, 'report_id', None), 'envelope report_id')
    original_id = getattr(envelope, 'original_id', None)
    body = getattr(envelope, 'body', None)
    if type(body) is not bytes:
        raise ValueError('report body must be bytes')
    if type(original_id) is not str or hashlib.sha256(body).hexdigest() != original_id:
        raise ValueError('original identity does not match report bytes')
    try:
        payload = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid UTF-8 report document') from error
    if type(payload) is not dict:
        raise ValueError('invalid report document')
    required = {'version', 'report_id', 'result_id', 'plant', 'day', 'batch',
                'revision_id', 'slots', 'bounds', 'allocations'}
    optional = {'rows', 'outlets', 'total'}
    if not required <= set(payload) or not set(payload) <= required | optional:
        raise ValueError('invalid shared report fields')
    if _integer(payload['version'], 'version', 1) != 2:
        raise ValueError('unsupported report version')
    if _text(payload['report_id'], 'report_id') != report_id:
        raise ValueError('report number does not match envelope')
    slots = _shared_slots(payload['slots'])
    bounds = _shared_bounds(payload['bounds'])
    allocations = _shared_allocations(payload['allocations'], slots)

    bound_outlets = {outlet for outlet, _, _ in bounds}
    for _, _, permits in slots:
        for outlet in permits:
            if outlet not in bound_outlets:
                raise ValueError(f'slot outlet {outlet} has no daily bound')

    by_slot = {ordinal: [] for ordinal, _, _ in slots}
    for ordinal, outlet, ml in allocations:
        by_slot[ordinal].append((outlet, ml))
    evidence = []
    for ordinal, slot_ml, permits in slots:
        parts = tuple(by_slot[ordinal])
        if sum(ml for _, ml in parts) != slot_ml:
            raise ValueError(f'slot {ordinal} allocation does not conserve its reading')
        evidence.append(SlotEvidence(ordinal, slot_ml, permits, parts))

    per_outlet = {outlet: 0 for outlet, _, _ in bounds}
    for _, outlet, ml in allocations:
        per_outlet[outlet] += ml
    for outlet, low, high in bounds:
        if not low <= per_outlet[outlet] <= high:
            raise ValueError(f'outlet {outlet} allocation leaves its daily bounds')

    projected = tuple(ReportRow(index, outlet, ml) for index, (outlet, ml) in enumerate(
        ((outlet, ml) for slot in evidence for outlet, ml in slot.allocation), start=1))
    summary = _summarize(projected)

    claimed_outlets = summary[0]
    if 'rows' in payload:
        if _rows(payload['rows']) != projected:
            raise ValueError('declared rows differ from the allocation evidence')
    if 'outlets' in payload:
        if _outlet_claims(payload['outlets']) != summary[0]:
            raise ValueError('declared outlet totals differ from the allocation evidence')
    if 'total' in payload:
        total = payload['total']
        _fields(total, {'row_count', 'ml'}, 'batch total')
        claimed_total = (_integer(total['row_count'], 'row_count', 0), _ml(total['ml']))
        if claimed_total != summary[1:]:
            raise ValueError('declared batch total differs from the allocation evidence')

    return DecodedSharedReport(
        report_id, _integer(payload['result_id'], 'result_id', 1),
        _text(payload['plant'], 'plant'), _text(payload['day'], 'day'),
        _text(payload['batch'], 'batch'), _integer(payload['revision_id'], 'revision_id', 1),
        tuple(evidence), bounds, projected, claimed_outlets, summary[1], summary[2],
    )


def _summarize(rows):
    per_outlet = {}
    total_count = 0
    total_ml = 0
    for row in rows:
        count, ml = per_outlet.get(row.outlet, (0, 0))
        per_outlet[row.outlet] = count + 1, ml + row.ml
        total_count += 1
        total_ml += row.ml
    outlets = tuple((outlet, count, ml)
                    for outlet, (count, ml) in sorted(per_outlet.items()))
    return outlets, total_count, total_ml


def report_version(envelope):
    """Peek at the declared version; malformed bodies fall through to decode_report."""
    body = getattr(envelope, 'body', None)
    if type(body) is not bytes:
        raise ValueError('report body must be bytes')
    try:
        payload = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid UTF-8 report document') from error
    if type(payload) is not dict or type(payload.get('version')) is not int:
        return 1
    return payload['version']


def decode_any_report(envelope):
    if report_version(envelope) == 2:
        return decode_shared_report(envelope)
    return decode_report(envelope)


def decode_report(envelope):
    report_id = _text(getattr(envelope, 'report_id', None), 'envelope report_id')
    original_id = getattr(envelope, 'original_id', None)
    body = getattr(envelope, 'body', None)
    if type(body) is not bytes:
        raise ValueError('report body must be bytes')
    if type(original_id) is not str or hashlib.sha256(body).hexdigest() != original_id:
        raise ValueError('original identity does not match report bytes')
    try:
        payload = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid UTF-8 report document') from error
    _fields(payload, {'version', 'report_id', 'result_id', 'plant', 'day', 'batch',
                      'revision_id', 'rows', 'outlets', 'total'}, 'report')
    if _integer(payload['version'], 'version', 1) != 1:
        raise ValueError('unsupported report version')
    if _text(payload['report_id'], 'report_id') != report_id:
        raise ValueError('report number does not match envelope')
    total = payload['total']
    _fields(total, {'row_count', 'ml'}, 'batch total')
    return DecodedReport(
        report_id, _integer(payload['result_id'], 'result_id', 1),
        _text(payload['plant'], 'plant'), _text(payload['day'], 'day'),
        _text(payload['batch'], 'batch'), _integer(payload['revision_id'], 'revision_id', 1),
        _rows(payload['rows']), _outlet_claims(payload['outlets']),
        _integer(total['row_count'], 'row_count', 0), _ml(total['ml']),
    )
