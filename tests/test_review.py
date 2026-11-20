from posting.ledger import get_ledger_totals
from posting.receiver_store import initialize_receiver, receiver_write
from posting.staging import stage_report
from review.discrepancies import Discrepancy
from review.reports import audit_report
from tests.support import WarehouseCase, readings


class TestReview(WarehouseCase):
    def _post_r6(self):
        return self.stage_and_post(self.issue(readings(('A', 5), ('A', 7), ('B', 3)),
                                              report_id='R6'))

    def _reopen_without_source(self):
        self.source.close()
        self.reopen_receiver()

    def test_reopen_without_source(self):
        posted = self._post_r6()
        self._reopen_without_source()
        audit = audit_report(self.receiver, 'R6')
        expected_rows = [(1, 'A', 5), (2, 'A', 7), (3, 'B', 3)]
        self.assertEqual([(r.ordinal, r.outlet, r.ml) for r in audit.original_rows], expected_rows)
        self.assertEqual([(r.ordinal, r.outlet, r.ml) for r in audit.ledger_rows], expected_rows)
        for totals in (audit.original_totals, audit.ledger_totals,
                       audit.original_computed, audit.ledger_computed):
            self.assertEqual((totals.row_count, totals.ml, totals.outlets),
                             (3, 15, (('A', 2, 12), ('B', 1, 3))))
        self.assertEqual(audit.receipt, posted.receipt)
        self.assertEqual(audit.discrepancies, ())

    def test_before_post(self):
        with self.assertRaises(KeyError):
            audit_report(self.receiver, 'R7')
        stage_report(self.receiver, self.issue().envelope)
        with self.assertRaises(KeyError):
            audit_report(self.receiver, 'R7')
        self.assertEqual(get_ledger_totals(self.receiver).ml, 0)

    def test_ordinal_mismatch(self):
        self._post_r6()
        with receiver_write(self.receiver):
            self.receiver.execute('''UPDATE ledger_rows
                SET ml = CASE ordinal WHEN 1 THEN '7' ELSE '5' END
                WHERE report_id = 'R6' AND ordinal IN (1, 2)''')
        self._reopen_without_source()
        audit = audit_report(self.receiver, 'R6')
        self.assertEqual([row.ml for row in audit.original_rows], [5, 7, 3])
        self.assertEqual(audit.ledger_computed.ml, 15)
        self.assertEqual(audit.discrepancies, (
            Discrepancy('row_mismatch', 1, ('A', 5), ('A', 7)),
            Discrepancy('row_mismatch', 2, ('A', 7), ('A', 5)),
        ))

    def test_missing_and_extra(self):
        self._post_r6()
        with receiver_write(self.receiver):
            self.receiver.execute("DELETE FROM ledger_rows WHERE report_id = 'R6' AND ordinal = 2")
            self.receiver.execute("INSERT INTO ledger_rows VALUES ('R6', 4, 'A', '7')")
        self._reopen_without_source()
        self.assertEqual(audit_report(self.receiver, 'R6').discrepancies, (
            Discrepancy('missing_row', 2, ('A', 7), None),
            Discrepancy('extra_row', 4, None, ('A', 7)),
        ))

    def test_fixed_total_discrepancies(self):
        self._post_r6()
        with receiver_write(self.receiver):
            self.receiver.execute("UPDATE posted_reports SET ml = '16' WHERE report_id = 'R6'")
            self.receiver.execute("UPDATE ledger_outlet_totals SET ml = '4' WHERE report_id = 'R6' AND outlet = 'B'")
        audit = audit_report(self.receiver, 'R6')
        self.assertEqual(audit.discrepancies, (
            Discrepancy('total_mismatch', ('ledger_fixed', 'batch', 'ml'), 16, 15),
            Discrepancy('total_mismatch', ('ledger_fixed', 'outlet', 'B', 'ml'), 4, 3),
        ))

    def test_empty_batch(self):
        posted = self.stage_and_post(self.issue([]))
        self._reopen_without_source()
        audit = audit_report(self.receiver, 'R7')
        self.assertEqual(audit.original_rows, ())
        self.assertEqual(audit.ledger_rows, ())
        self.assertEqual(audit.ledger_totals.outlets, ())
        self.assertEqual((audit.ledger_totals.row_count, audit.ledger_totals.ml), (0, 0))
        self.assertEqual(audit.receipt, posted.receipt)
        self.assertEqual(audit.discrepancies, ())

    def test_initialization_preserves_posted(self):
        self._post_r6()
        before = audit_report(self.receiver, 'R6')
        pending = self.issue()
        stage_id = stage_report(self.receiver, pending.envelope)
        self._reopen_without_source()
        initialize_receiver(self.receiver)
        initialize_receiver(self.receiver)
        self.assertEqual(audit_report(self.receiver, 'R6'), before)
        self.assertEqual(stage_report(self.receiver, pending.envelope), stage_id)
        with self.assertRaises(KeyError):
            audit_report(self.receiver, 'R7')
        totals = get_ledger_totals(self.receiver)
        self.assertEqual((totals.row_count, totals.ml), (3, 15))
