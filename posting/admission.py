from posting.report_totals import ReportTotals, summarize_rows
from posting.wire_format import decode_any_report, decode_report, report_version


def validate_report(envelope):
    """Validate a V1 or V2 original; V2 evidence is checked in full on decode."""
    if report_version(envelope) == 2:
        return decode_any_report(envelope)
    report = decode_report(envelope)
    declared = ReportTotals(report.declared_outlets, report.declared_row_count, report.declared_ml)
    if summarize_rows(report.rows) != declared:
        raise ValueError('declared report totals differ from the original rows')
    return report
