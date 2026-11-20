import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from posting.receipts import PostingReceipt, create_receipt, get_receipt
from posting.receiver_store import receiver_read, receiver_write
from posting.report_totals import ReportTotals, summarize_rows
from posting.staging import get_staged_report
from posting.wire_format import ReportRow, decode_any_report, decode_report


@dataclass(frozen=True)
class PostedReport:
    report_id: str
    stage_id: int
    rows: tuple[ReportRow, ...]
    totals: ReportTotals
    receipt: PostingReceipt


def _ledger_rows(records):
    return (ReportRow(ordinal, outlet, int(Decimal(ml))) for ordinal, outlet, ml in records)


def get_posted_report(db, report_id):
    with receiver_read(db):
        header = db.execute('''SELECT s.stage_id, p.row_count, p.ml
            FROM posted_reports AS p JOIN staged_reports AS s USING (report_id)
            WHERE p.report_id = ?''', (report_id,)).fetchone()
        if header is None:
            return None
        records = db.execute('''SELECT ordinal, outlet, ml FROM ledger_rows
            WHERE report_id = ? ORDER BY ordinal''', (report_id,))
        rows = tuple(_ledger_rows(records))
        records = db.execute('''SELECT outlet, row_count, ml FROM ledger_outlet_totals
            WHERE report_id = ? ORDER BY outlet''', (report_id,))
        outlets = tuple((outlet, count, int(Decimal(ml))) for outlet, count, ml in records)
        totals = ReportTotals(outlets, header['row_count'], int(Decimal(header['ml'])))
        receipt = get_receipt(db, report_id)
        if receipt is None:
            raise sqlite3.IntegrityError(f'posted report {report_id} has no receipt')
        return PostedReport(report_id, header['stage_id'], rows, totals, receipt)


def post_report(db, stage_id):
    with receiver_write(db):
        staged = get_staged_report(db, stage_id)
        if staged is None:
            raise KeyError(f'unknown staged report {stage_id}')
        existing = get_posted_report(db, staged.report_id)
        if existing is not None:
            return existing
        report = decode_any_report(staged)
        totals = summarize_rows(report.rows)
        db.execute('INSERT INTO posted_reports (report_id, row_count, ml) VALUES (?, ?, ?)',
                   (report.report_id, totals.row_count, str(Decimal(totals.ml))))
        db.executemany('''INSERT INTO ledger_rows (report_id, ordinal, outlet, ml)
            VALUES (?, ?, ?, ?)''',
            ((report.report_id, row.ordinal, row.outlet, str(Decimal(row.ml))) for row in report.rows))
        db.executemany('''INSERT INTO ledger_outlet_totals (report_id, outlet, row_count, ml)
            VALUES (?, ?, ?, ?)''',
            ((report.report_id, outlet, count, str(Decimal(ml))) for outlet, count, ml in totals.outlets))
        receipt = create_receipt(db, report.report_id, staged.original_id)
        return PostedReport(report.report_id, stage_id, report.rows, totals, receipt)


def get_ledger_totals(db, report_id=None):
    sql = '''SELECT r.ordinal, r.outlet, r.ml FROM ledger_rows AS r
        JOIN posted_reports AS p ON p.report_id = r.report_id'''
    params = ()
    if report_id is not None:
        sql += ' WHERE p.report_id = ?'
        params = (report_id,)
    return summarize_rows(_ledger_rows(db.execute(sql, params)))
