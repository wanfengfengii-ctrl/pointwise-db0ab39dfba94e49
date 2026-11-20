import sqlite3
from dataclasses import dataclass

from posting.ledger import PostedReport, get_posted_report
from posting.receiver_store import receiver_read
from posting.staging import get_staged_report
from posting.wire_format import decode_any_report


@dataclass(frozen=True)
class AuditInputs:
    original: object
    posted: PostedReport
    original_id: str


def load_audit_inputs(db, report_id):
    with receiver_read(db):
        posted = get_posted_report(db, report_id)
        if posted is None:
            return None
        staged = get_staged_report(db, posted.stage_id)
        if staged is None:
            raise sqlite3.IntegrityError(f'posted report {report_id} has no staged original')
        return AuditInputs(decode_any_report(staged), posted, staged.original_id)
