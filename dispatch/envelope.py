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
