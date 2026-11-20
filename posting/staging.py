from dataclasses import dataclass

from posting.admission import validate_report
from posting.receiver_store import receiver_write


@dataclass(frozen=True)
class StagedReport:
    stage_id: int
    report_id: str
    original_id: str
    body: bytes


def stage_report(db, envelope):
    report = validate_report(envelope)
    with receiver_write(db):
        existing = db.execute('''SELECT stage_id, original_id, body FROM staged_reports
            WHERE report_id = ?''', (report.report_id,)).fetchone()
        if existing is not None:
            if (existing['original_id'], existing['body']) != (envelope.original_id, envelope.body):
                raise ValueError('report number already has a different original')
            return existing['stage_id']
        cursor = db.execute('''INSERT INTO staged_reports (report_id, original_id, body)
            VALUES (?, ?, ?)''', (report.report_id, envelope.original_id, envelope.body))
        return cursor.lastrowid


def get_staged_report(db, stage_id):
    if type(stage_id) is not int or not 0 < stage_id <= 2**63 - 1:
        raise ValueError('stage_id must be a positive SQLite integer')
    record = db.execute('''SELECT stage_id, report_id, original_id, body FROM staged_reports
        WHERE stage_id = ?''', (stage_id,)).fetchone()
    return None if record is None else StagedReport(*record)
