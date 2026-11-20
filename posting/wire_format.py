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
