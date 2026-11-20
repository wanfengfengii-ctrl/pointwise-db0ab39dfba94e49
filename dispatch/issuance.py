from closeout.results import get_result
from dispatch.envelope import build_envelope
from dispatch.outbox import get_issued_report, insert_issued_report
from intake.source_store import require_id, require_name, source_write


def issue_report(db, report_id, result_id):
    require_name(report_id, 'report_id')
    require_id(result_id, 'result_id')
    with source_write(db):
        existing = get_issued_report(db, report_id)
        if existing is not None:
            if existing.result_id != result_id:
                raise ValueError('report number already has a different result')
            return existing
        result = get_result(db, result_id)
        if result is None:
            raise KeyError(f'unknown closeout result {result_id}')
        return insert_issued_report(db, result_id, build_envelope(report_id, result))
