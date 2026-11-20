from dispatch.acknowledgments import confirm_receipt
from dispatch.outbox import get_issued_report
from posting.ledger import get_ledger_totals
from posting.receipts import get_receipt
from review.reports import audit_report
from tests.support import LocalDelivery, WarehouseCase, readings


class TestHandoff(WarehouseCase):
    def test_response_lost(self):
        issued = self.issue()
        delivery = LocalDelivery()
        self.assertIsNone(delivery.send(self.receiver, issued.envelope, hold_reply=True))
        self.assertEqual(get_issued_report(self.source, 'R7'), issued)
        self.assertEqual(issued.status, 'pending')
        before = get_ledger_totals(self.receiver)
        self.assertEqual((before.row_count, before.ml), (2, 12))
        self.reopen_receiver()
        self.reopen_source()
        pending = get_issued_report(self.source, 'R7')
        self.assertEqual(pending, issued)
        persisted_receipt = get_receipt(self.receiver, 'R7')
        replayed = delivery.send(self.receiver, pending.envelope)
        self.assertEqual(replayed, persisted_receipt.to_payload())
        confirmation = confirm_receipt(self.source, 'R7', replayed)
        self.assertEqual(confirm_receipt(self.source, 'R7', delivery.release_reply()), confirmation)
        self.assertEqual(get_ledger_totals(self.receiver), before)
        self.reopen_source()
        self.assertEqual(get_issued_report(self.source, 'R7').confirmation, confirmation)

    def test_request_held(self):
        issued = self.issue()
        delivery = LocalDelivery()
        self.assertIsNone(delivery.send(self.receiver, issued.envelope, hold_request=True))
        self.assertEqual(get_issued_report(self.source, 'R7').status, 'pending')
        self.assertIsNone(get_receipt(self.receiver, 'R7'))
        self.reopen_receiver()
        self.reopen_source()
        self.assertEqual(get_issued_report(self.source, 'R7'), issued)
        before = get_ledger_totals(self.receiver)
        self.assertEqual((before.row_count, before.ml), (0, 0))
        receipt = delivery.release_request(self.receiver)
        confirm_receipt(self.source, 'R7', receipt)
        self.assertEqual(get_issued_report(self.source, 'R7').status, 'confirmed')
        self.source.close()
        audit = audit_report(self.receiver, 'R7')
        self.assertEqual((audit.ledger_totals.row_count, audit.ledger_totals.ml), (2, 12))
        self.assertEqual(audit.discrepancies, ())

    def test_wrong_receipt_binding(self):
        issued = self.issue()
        delivery = LocalDelivery()
        self.assertIsNone(delivery.send(self.receiver, issued.envelope, hold_reply=True))
        real = delivery.release_reply()
        wrong_receipts = (dict(real, original_id='0' * 64), dict(real, report_id='R8'))
        for wrong in wrong_receipts:
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'does not match'):
                confirm_receipt(self.source, 'R7', wrong)
        self.reopen_source()
        self.assertEqual(get_issued_report(self.source, 'R7'), issued)
        replayed = delivery.send(self.receiver, issued.envelope)
        self.assertEqual(replayed, real)
        confirmed = confirm_receipt(self.source, 'R7', replayed)
        with self.assertRaisesRegex(ValueError, 'different receipt'):
            confirm_receipt(self.source, 'R7', dict(real, receipt_id=real['receipt_id'] + 1))
        self.reopen_source()
        self.assertEqual(get_issued_report(self.source, 'R7').confirmation, confirmed)

    def test_integer_precision_across_both_warehouses(self):
        large = 10**4500 + 7
        issued = self.issue(readings(('A', large), ('B', -large), ('A', 4)))
        posted = self.stage_and_post(issued)
        confirm_receipt(self.source, 'R7', posted.receipt.to_payload())
        self.source.close()
        self.reopen_receiver()
        audit = audit_report(self.receiver, 'R7')
        self.assertTrue(audit.ledger_rows[0].ml == large)
        self.assertTrue(audit.ledger_rows[1].ml == -large)
        expected_outlets = (('A', 2, large + 4), ('B', 1, -large))
        self.assertTrue(audit.ledger_totals.outlets == expected_outlets)
        self.assertEqual((audit.ledger_totals.row_count, audit.ledger_totals.ml), (3, 4))
        self.assertEqual(audit.discrepancies, ())
