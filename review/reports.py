from dataclasses import dataclass

from posting.receipts import PostingReceipt
from posting.report_totals import ReportTotals
from posting.wire_format import ReportRow
from review.discrepancies import Discrepancy
from review.received import load_audit_inputs
from review.reconciliation import reconcile_report


@dataclass(frozen=True)
class AuditReport:
    report_id: str
    original_rows: tuple[ReportRow, ...]
    ledger_rows: tuple[ReportRow, ...]
    original_totals: ReportTotals
    ledger_totals: ReportTotals
    original_computed: ReportTotals
    ledger_computed: ReportTotals
    receipt: PostingReceipt
    discrepancies: tuple[Discrepancy, ...]


def audit_report(db, report_id):
    inputs = load_audit_inputs(db, report_id)
    if inputs is None:
        raise KeyError(f'report {report_id} is not posted')
    original = inputs.original
    posted = inputs.posted
    reconciliation = reconcile_report(inputs)
    original_totals = ReportTotals(original.declared_outlets, original.declared_row_count,
                                   original.declared_ml)
    return AuditReport(report_id, original.rows, posted.rows, original_totals,
                       posted.totals, reconciliation.original_computed,
                       reconciliation.ledger_computed, posted.receipt,
                       reconciliation.discrepancies)
