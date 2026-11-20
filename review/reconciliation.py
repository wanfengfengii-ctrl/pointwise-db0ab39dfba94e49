from dataclasses import dataclass

from posting.report_totals import ReportTotals, summarize_rows
from review.discrepancies import Discrepancy, compare_rows, compare_totals
from review.received import AuditInputs


@dataclass(frozen=True)
class Reconciliation:
    original_computed: ReportTotals
    ledger_computed: ReportTotals
    discrepancies: tuple[Discrepancy, ...]


def reconcile_report(inputs: AuditInputs):
    original = inputs.original
    posted = inputs.posted
    declared = ReportTotals(original.declared_outlets, original.declared_row_count, original.declared_ml)
    original_computed = summarize_rows(original.rows)
    ledger_computed = summarize_rows(posted.rows)
    differences = (
        compare_rows(original.rows, posted.rows)
        + compare_totals(declared, original_computed, 'original_declared')
        + compare_totals(posted.totals, ledger_computed, 'ledger_fixed')
        + compare_totals(original_computed, ledger_computed, 'original_to_ledger')
    )
    if posted.receipt.original_id != inputs.original_id:
        differences += (Discrepancy('receipt_mismatch', ('receipt', 'original_id'),
                                     inputs.original_id, posted.receipt.original_id),)
    return Reconciliation(original_computed, ledger_computed, differences)
