import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Barrier

from dispatch.acknowledgments import confirm_receipt
from posting.ledger import get_ledger_totals, get_posted_report, post_report
from posting.receipts import get_receipt
from posting.receiver_store import open_receiver
from posting.staging import get_staged_report, stage_report
from tests.support import CommitFailureConnection, WarehouseCase, edit_body, readings, rewrap


class TestPosting(WarehouseCase):
    def _assert_no_delivery(self):
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM staged_reports').fetchone()[0], 0)
        self._assert_unposted()

    def _assert_unposted(self):
        totals = get_ledger_totals(self.receiver)
        self.assertEqual((totals.row_count, totals.ml), (0, 0))
        self.assertIsNone(get_receipt(self.receiver, 'R7'))
        self.assertIsNone(get_posted_report(self.receiver, 'R7'))

    def test_reject_wrong_total(self):
        envelope = self.issue().envelope
        invalid = edit_body(envelope, lambda data: data['total'].update(ml='13'))
        with self.assertRaisesRegex(ValueError, 'declared report totals'):
            stage_report(self.receiver, invalid)
        self._assert_no_delivery()
        stage_report(self.receiver, envelope)
        self._assert_unposted()

    def test_wrong_row_count(self):
        envelope = self.issue().envelope
        edits = [lambda data: data['total'].update(row_count=1),
                 lambda data: data['outlets'][0].update(row_count=1)]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaisesRegex(ValueError, 'declared report totals'):
                stage_report(self.receiver, edit_body(envelope, edit))
        self._assert_no_delivery()

    def test_wrong_outlet_totals(self):
        envelope = self.issue(readings(('A', 5), ('B', 7))).envelope

        def swap_outlet_amounts(data):
            data['outlets'][0]['ml'] = '7'
            data['outlets'][1]['ml'] = '5'

        with self.assertRaisesRegex(ValueError, 'declared report totals'):
            stage_report(self.receiver, edit_body(envelope, swap_outlet_amounts))
        self._assert_no_delivery()

    def test_invalid_encoding(self):
        envelope = self.issue().envelope
        malformed = (
            rewrap(envelope, b'\xff'),
            rewrap(envelope, envelope.body[:-1] + b',"version":1}'),
            replace(envelope, original_id='0' * 64),
            replace(envelope, report_id='R8'),
            edit_body(envelope, lambda data: data['rows'][0].update(ordinal=2)),
            edit_body(envelope, lambda data: data['rows'][0].update(ml=True)),
            edit_body(envelope, lambda data: data.pop('revision_id')),
        )
        for bad in malformed:
            with self.subTest(envelope=bad), self.assertRaises(ValueError):
                stage_report(self.receiver, bad)
        self._assert_no_delivery()

    def test_stage_replay(self):
        envelope = self.issue().envelope
        stage_id = stage_report(self.receiver, envelope)
        self._assert_unposted()
        self.reopen_receiver()
        self.assertEqual(stage_report(self.receiver, envelope), stage_id)
        staged = get_staged_report(self.receiver, stage_id)
        self.assertEqual((staged.report_id, staged.original_id, staged.body),
                         ('R7', envelope.original_id, envelope.body))
        self._assert_unposted()

    def test_same_number_different_body(self):
        envelope = self.issue().envelope
        stage_id = stage_report(self.receiver, envelope)

        def change_rows(data):
            data['rows'][0]['ml'] = '4'
            data['rows'][1]['ml'] = '8'

        other = edit_body(envelope, change_rows)
        with self.assertRaisesRegex(ValueError, 'different original'):
            stage_report(self.receiver, other)
        self.assertEqual(stage_report(self.receiver, envelope), stage_id)
        self.assertEqual(get_staged_report(self.receiver, stage_id).body, envelope.body)
        posted = post_report(self.receiver, stage_id)
        self.assertEqual([row.ml for row in posted.rows], [5, 7])
        with self.assertRaisesRegex(ValueError, 'different original'):
            stage_report(self.receiver, other)
        self.assertEqual(get_posted_report(self.receiver, 'R7'), posted)

    def test_receipt_replay(self):
        issued = self.issue()
        stage_id = stage_report(self.receiver, issued.envelope)
        first = post_report(self.receiver, stage_id)
        self.assertEqual(stage_report(self.receiver, issued.envelope), stage_id)
        repeated = post_report(self.receiver, stage_id)
        self.assertEqual(repeated, first)
        confirmed = confirm_receipt(self.source, 'R7', repeated.receipt.to_payload())
        self.assertEqual((confirmed.report_id, confirmed.original_id, confirmed.receipt_id),
                         ('R7', issued.envelope.original_id, first.receipt.receipt_id))
        totals = get_ledger_totals(self.receiver)
        self.assertEqual((totals.row_count, totals.ml, totals.outlets), (2, 12, (('A', 2, 12),)))

    def test_original_receipt_identity(self):
        issued = self.issue()
        first = self.stage_and_post(issued)
        self.reopen_receiver()
        stage_id = stage_report(self.receiver, issued.envelope)
        self.assertEqual(stage_id, first.stage_id)
        self.assertEqual(post_report(self.receiver, stage_id), first)
        self.assertEqual(get_receipt(self.receiver, 'R7'), first.receipt)
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_unknown_stage(self):
        with self.assertRaises(KeyError):
            post_report(self.receiver, 99)
        self._assert_no_delivery()

    def _fail_posting_commit(self):
        envelope = self.issue().envelope
        stage_id = stage_report(self.receiver, envelope)
        self.reopen_receiver(factory=CommitFailureConnection)

        def observe(writer):
            self.assertEqual(get_receipt(writer, 'R7').original_id, envelope.original_id)
            tentative = get_ledger_totals(writer)
            self.assertEqual((tentative.row_count, tentative.ml), (2, 12))
            for table, count in (('posted_reports', 1), ('ledger_outlet_totals', 1)):
                self.assertEqual(writer.execute(f'SELECT count(*) FROM {table}').fetchone()[0], count)
            with closing(open_receiver(self.receiver_path)) as observer:
                self.assertIsNone(get_receipt(observer, 'R7'))
                self.assertEqual(get_ledger_totals(observer).row_count, 0)

        self.receiver.arm(observe)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            post_report(self.receiver, stage_id)
        self._assert_unposted()
        return stage_id, envelope

    def _assert_retry(self, stage_id, envelope):
        self.assertEqual(get_staged_report(self.receiver, stage_id).body, envelope.body)
        posted = post_report(self.receiver, stage_id)
        self.assertEqual(post_report(self.receiver, stage_id), posted)
        self.assertEqual(get_receipt(self.receiver, 'R7'), posted.receipt)
        totals = get_ledger_totals(self.receiver)
        self.assertEqual((totals.row_count, totals.ml), (2, 12))

    def test_failed_commit(self):
        stage_id, envelope = self._fail_posting_commit()
        self._assert_retry(stage_id, envelope)

    def test_failed_commit_after_reopen(self):
        stage_id, envelope = self._fail_posting_commit()
        self.reopen_receiver()
        self._assert_unposted()
        self._assert_retry(stage_id, envelope)

    def test_concurrent_replay(self):
        stage_id = stage_report(self.receiver, self.issue().envelope)
        barrier = Barrier(2, timeout=5)

        def post_from_separate_connection():
            with closing(open_receiver(self.receiver_path)) as receiver:
                barrier.wait()
                return post_report(receiver, stage_id)

        with ThreadPoolExecutor(max_workers=2) as workers:
            tasks = [workers.submit(post_from_separate_connection) for _ in range(2)]
            first, second = [task.result(timeout=15) for task in tasks]
        self.assertEqual(first, second)
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)
        totals = get_ledger_totals(self.receiver)
        self.assertEqual((totals.row_count, totals.ml), (2, 12))
