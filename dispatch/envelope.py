import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReportEnvelope:
    report_id: str
    original_id: str
    body: bytes


def build_envelope(report_id, result):
    revision = result.revision
    totals = result.totals
    if result.shared is not None:
        return _build_shared_envelope(report_id, result, revision, totals)
    payload = {
        'version': 1,
        'report_id': report_id,
        'result_id': result.result_id,
        'plant': revision.plant,
        'day': revision.day,
        'batch': revision.batch,
        'revision_id': revision.revision_id,
        'rows': [
            {'ordinal': row.ordinal, 'outlet': row.outlet, 'ml': str(Decimal(row.ml))}
            for row in revision.rows
        ],
        'outlets': [
            {'outlet': item.outlet, 'row_count': item.row_count, 'ml': str(Decimal(item.ml))}
            for item in totals.outlets
        ],
        'total': {'row_count': totals.row_count, 'ml': str(Decimal(totals.ml))},
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    return ReportEnvelope(report_id, hashlib.sha256(body).hexdigest(), body)


def _build_shared_envelope(report_id, result, revision, totals):
    """V2 body: the whole fixed day plus independently checkable allocation evidence.

    ``allocations`` is the per-slot split, sorted by slot then outlet, carrying
    only positive parts.  ``rows`` is its projection in that same order; both are
    re-derived and cross-checked by any receiver before it accepts the body.
    """
    shared = result.shared
    allocations = [
        {'ordinal': slot.ordinal, 'outlet': outlet, 'ml': str(Decimal(ml))}
        for slot in shared.slots for outlet, ml in slot.allocation
    ]
    payload = {
        'version': 2,
        'report_id': report_id,
        'result_id': result.result_id,
        'plant': revision.plant,
        'day': revision.day,
        'batch': revision.batch,
        'revision_id': revision.revision_id,
        'slots': [
            {'ordinal': slot.ordinal, 'ml': str(Decimal(slot.ml)),
             'outlets': list(slot.outlets)}
            for slot in shared.slots
        ],
        'bounds': [
            {'outlet': outlet, 'min_ml': str(Decimal(low)), 'max_ml': str(Decimal(high))}
            for outlet, low, high in shared.bounds
        ],
        'allocations': allocations,
        'rows': [
            {'ordinal': row.ordinal, 'outlet': row.outlet, 'ml': str(Decimal(row.ml))}
            for row in shared.rows
        ],
        'outlets': [
            {'outlet': item.outlet, 'row_count': item.row_count, 'ml': str(Decimal(item.ml))}
            for item in totals.outlets
        ],
        'total': {'row_count': totals.row_count, 'ml': str(Decimal(totals.ml))},
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    return ReportEnvelope(report_id, hashlib.sha256(body).hexdigest(), body)
