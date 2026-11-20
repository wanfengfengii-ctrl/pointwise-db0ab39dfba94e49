from collections.abc import Mapping

from dispatch.outbox import get_issued_report, save_confirmation
from intake.source_store import require_id, require_name, source_write


def confirm_receipt(db, report_id, receipt):
    require_name(report_id, 'report_id')
    if not isinstance(receipt, Mapping) or set(receipt) != {'report_id', 'original_id', 'receipt_id'}:
        raise ValueError('invalid posting receipt fields')
    require_name(receipt['report_id'], 'receipt report_id')
    require_name(receipt['original_id'], 'receipt original_id')
    receipt_id = require_id(receipt['receipt_id'], 'receipt_id')
    with source_write(db):
        issued = get_issued_report(db, report_id)
        if issued is None:
            raise KeyError(f'unknown issued report {report_id}')
        if receipt['report_id'] != report_id or receipt['original_id'] != issued.envelope.original_id:
            raise ValueError('receipt does not match the issued original')
        return save_confirmation(db, issued, receipt_id)
