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
    slot_allocations: tuple = ()


def audit_report(db, report_id):
    inputs = load_audit_inputs(db, report_id)
    if inputs is None:
        raise KeyError(f'report {report_id} is not posted')
    original = inputs.original
    posted = inputs.posted
    reconciliation = reconcile_report(inputs)
    original_totals = ReportTotals(original.declared_outlets, original.declared_row_count,
                                   original.declared_ml)
    slot_allocations = ()
    if getattr(original, 'slots', None) is not None:
        slot_allocations = _slot_allocations(original)
    return AuditReport(report_id, original.rows, posted.rows, original_totals,
                       posted.totals, reconciliation.original_computed,
                       reconciliation.ledger_computed, posted.receipt,
                       reconciliation.discrepancies, slot_allocations)


def _slot_allocations(original):
    """Attribute each formal ledger row back to its originating slot.

    Evidence is projected in slot then outlet order; every positive part becomes
    the next consecutively numbered ledger row, exactly as posting lays it down.
    A zero-reading slot keeps an empty destination tuple.
    """
    allocations = []
    ordinal = 0
    for slot in original.slots:
        items = []
        for outlet, ml in slot.allocation:
            ordinal += 1
            items.append((ordinal, outlet, ml))
        allocations.append((slot.ordinal, slot.ml, tuple(items)))
    return tuple(allocations)
