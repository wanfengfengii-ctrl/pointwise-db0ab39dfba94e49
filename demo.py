import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from closeout.jobs import close_batch
from closeout.results import get_result, initialize_closeout
from dispatch.acknowledgments import confirm_receipt
from dispatch.issuance import issue_report
from dispatch.outbox import get_issued_report, initialize_outbox
from intake.batches import accept_batch
from intake.outlets import register_outlet
from intake.source_store import initialize_intake, open_source
from posting.ledger import get_ledger_totals, post_report
from posting.receiver_store import initialize_receiver, open_receiver
from posting.staging import stage_report
from review.reports import audit_report


def _measure(totals):
    return {'rows': totals.row_count, 'ml': totals.ml}


def _expect(label, expected, actual):
    if actual != expected:
        raise SystemExit(f'{label}: expected {expected!r}, got {actual!r}')


def _scenario(case_id, data, expected, actual, downstream_expected, downstream_actual,
              code_paths, oracle):
    _expect(case_id, expected, actual)
    _expect(f'{case_id} downstream', downstream_expected, downstream_actual)
    record = {'case_id': case_id, 'input': data, 'expected': expected, 'actual': actual,
              'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
              'code_paths': code_paths, 'oracle': oracle}
    print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False, sort_keys=True))


def main():
    sample_path = Path(__file__).with_name('samples') / 'day.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    plant, day, batch = sample['plant'], sample['day'], sample['batch']
    with TemporaryDirectory() as directory:
        source_path = Path(directory) / 'source.sqlite3'
        receiver_path = Path(directory) / 'receiver.sqlite3'
        with closing(open_source(source_path)) as source, closing(open_receiver(receiver_path)) as receiver:
            initialize_intake(source)
            initialize_closeout(source)
            initialize_outbox(source)
            initialize_receiver(receiver)
            for outlet in sample['outlets']:
                register_outlet(source, plant, outlet)
            first = accept_batch(source, plant, day, batch, sample['original'])
            corrected = accept_batch(source, plant, day, batch, sample['correction'], parent_id=first)
            old_result = close_batch(source, first)
            new_result = close_batch(source, corrected)
            issued = issue_report(source, 'R6', old_result.result_id)
            stage_id = stage_report(receiver, issued.envelope)
            _expect('暂存时的监管账', {'rows': 0, 'ml': 0}, _measure(get_ledger_totals(receiver)))
            first_posting = post_report(receiver, stage_id)
            replay = post_report(receiver, stage_report(receiver, issued.envelope))
            _expect('重领原入账', first_posting, replay)
            confirm_receipt(source, 'R6', replay.receipt.to_payload())
            _expect('源仓交班', 'confirmed', get_issued_report(source, 'R6').status)

            actual = {**_measure(old_result.totals), 'outlets': [
                [item.outlet, item.row_count, item.ml] for item in old_result.totals.outlets
            ]}
            _scenario('case_close', sample['original'],
                      {'rows': 3, 'ml': 15, 'outlets': [['A', 2, 12], ['B', 1, 3]]}, actual,
                      {'rows': 3, 'ml': 15}, _measure(get_ledger_totals(receiver, 'R6')),
                      ['closeout/jobs.py', 'closeout/grouping.py', 'closeout/totals.py', 'posting/ledger.py'],
                      'A 为 5 + 7，B 为 3，三行合计 15 mL。')
            recalled = get_result(source, old_result.result_id)
            _scenario('case_revision', {'original': sample['original'], 'correction': sample['correction']},
                      [15, 16], [recalled.totals.ml, new_result.totals.ml],
                      {'rows': 3, 'ml': 15}, _measure(get_ledger_totals(receiver, 'R6')),
                      ['intake/batches.py', 'closeout/results.py', 'dispatch/issuance.py', 'posting/ledger.py'],
                      '将 7 改成 8 只给新核定加 1 mL，R6 选择的是旧核定。')

            night_revision = accept_batch(source, plant, day, sample['followup_batch'], sample['followup'])
            night_result = close_batch(source, night_revision)
            night = issue_report(source, 'R7', night_result.result_id)
            night_posting = post_report(receiver, stage_report(receiver, night.envelope))
            confirm_receipt(source, 'R7', night_posting.receipt.to_payload())

        with closing(open_receiver(receiver_path)) as receiver:
            audit = audit_report(receiver, 'R6')
            rows = [[row.ordinal, row.outlet, row.ml] for row in audit.original_rows]
            downstream = {**_measure(audit.ledger_computed), 'differences': len(audit.discrepancies),
                          'original_receipt': audit.receipt == first_posting.receipt}
            _scenario('case_reopen', {'report_id': 'R6', 'rows': sample['original']},
                      [[1, 'A', 5], [2, 'A', 7], [3, 'B', 3]], rows,
                      {'rows': 3, 'ml': 15, 'differences': 0, 'original_receipt': True}, downstream,
                      ['review/received.py', 'review/reconciliation.py', 'review/reports.py'],
                      '按样例原序比较三行，手算 5 + 7 + 3，并与首次领取的正式回执比较。')
            night_audit = audit_report(receiver, 'R7')
            _expect('R7 原序', [[1, 'A', 5], [2, 'A', 7]],
                    [[row.ordinal, row.outlet, row.ml] for row in night_audit.ledger_rows])
            _expect('R7 重开核账', {'rows': 2, 'ml': 12}, _measure(night_audit.ledger_computed))
            _expect('R7 差异', (), night_audit.discrepancies)
            _expect('双报送监管账', {'rows': 5, 'ml': 27}, _measure(get_ledger_totals(receiver)))
            print('断源重开后，R6 为 3 行 15 mL，R7 为 2 行 12 mL，核账均无差异。')


if __name__ == '__main__':
    main()
