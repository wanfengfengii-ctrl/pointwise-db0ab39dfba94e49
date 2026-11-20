from dataclasses import dataclass

from dispatch.envelope import ReportEnvelope
from intake.source_store import source_write


@dataclass(frozen=True)
class ReceiptConfirmation:
    report_id: str
    original_id: str
    receipt_id: int


@dataclass(frozen=True)
class IssuedReport:
    result_id: int
    envelope: ReportEnvelope
    confirmation: ReceiptConfirmation | None

    @property
    def status(self):
        return 'pending' if self.confirmation is None else 'confirmed'


def initialize_outbox(db):
    with source_write(db):
        db.execute('''CREATE TABLE IF NOT EXISTS issued_reports (
            report_id TEXT PRIMARY KEY NOT NULL,
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            original_id TEXT NOT NULL,
            body BLOB NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS confirmations (
            report_id TEXT PRIMARY KEY NOT NULL REFERENCES issued_reports(report_id),
            receipt_id INTEGER NOT NULL CHECK (receipt_id > 0)
        )''')


def get_issued_report(db, report_id):
    record = db.execute('''SELECT i.result_id, i.original_id, i.body, c.receipt_id
        FROM issued_reports AS i LEFT JOIN confirmations AS c USING (report_id)
        WHERE i.report_id = ?''', (report_id,)).fetchone()
    if record is None:
        return None
    envelope = ReportEnvelope(report_id, record['original_id'], record['body'])
    receipt_id = record['receipt_id']
    confirmation = None
    if receipt_id is not None:
        confirmation = ReceiptConfirmation(report_id, envelope.original_id, receipt_id)
    return IssuedReport(record['result_id'], envelope, confirmation)


def insert_issued_report(db, result_id, envelope):
    db.execute('''INSERT INTO issued_reports (report_id, result_id, original_id, body)
        VALUES (?, ?, ?, ?)''', (envelope.report_id, result_id, envelope.original_id, envelope.body))
    return IssuedReport(result_id, envelope, None)


def save_confirmation(db, issued, receipt_id):
    if issued.confirmation is not None:
        if issued.confirmation.receipt_id != receipt_id:
            raise ValueError('report already has a different receipt')
        return issued.confirmation
    envelope = issued.envelope
    db.execute('INSERT INTO confirmations (report_id, receipt_id) VALUES (?, ?)',
               (envelope.report_id, receipt_id))
    return ReceiptConfirmation(envelope.report_id, envelope.original_id, receipt_id)
