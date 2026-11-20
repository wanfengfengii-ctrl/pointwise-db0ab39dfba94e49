from dataclasses import dataclass


@dataclass(frozen=True)
class PostingReceipt:
    receipt_id: int
    report_id: str
    original_id: str

    def to_payload(self):
        return {'receipt_id': self.receipt_id, 'report_id': self.report_id,
                'original_id': self.original_id}


def create_receipt(db, report_id, original_id):
    """Register the original receipt inside the posting transaction."""
    cursor = db.execute('INSERT INTO receipts (report_id, original_id) VALUES (?, ?)',
                        (report_id, original_id))
    return PostingReceipt(cursor.lastrowid, report_id, original_id)


def get_receipt(db, report_id):
    record = db.execute('''SELECT r.receipt_id, r.report_id, r.original_id
        FROM receipts AS r JOIN posted_reports AS p USING (report_id)
        WHERE r.report_id = ?''', (report_id,)).fetchone()
    return None if record is None else PostingReceipt(*record)
