from posting.report_totals import ReportTotals, summarize_rows
from posting.wire_format import decode_report


def validate_report(envelope):
    report = decode_report(envelope)
    declared = ReportTotals(report.declared_outlets, report.declared_row_count, report.declared_ml)
    if summarize_rows(report.rows) != declared:
        raise ValueError('declared report totals differ from the original rows')
    return report
