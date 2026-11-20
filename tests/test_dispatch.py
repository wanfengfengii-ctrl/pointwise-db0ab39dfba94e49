import hashlib
import json

from closeout.jobs import close_batch
from dispatch.acknowledgments import confirm_receipt
from dispatch.issuance import issue_report
from dispatch.outbox import get_issued_report
from tests.support import WarehouseCase, readings


class TestDispatch(WarehouseCase):
    def _two_results(self):
        revision = self.accept(readings(('A', 5), ('A', 7)))
        first = close_batch(self.source, revision)
        corrected = self.accept(readings(('A', 5), ('A', 8)), parent_id=revision)
        return first, close_batch(self.source, corrected)

    def test_conflicting_report_binding(self):
        first, second = self._two_results()
        original = issue_report(self.source, 'R7', first.result_id)
        with self.assertRaisesRegex(ValueError, 'different result'):
            issue_report(self.source, 'R7', second.result_id)
        self.assertEqual(get_issued_report(self.source, 'R7'), original)
        self.assertEqual(original.status, 'pending')

    def test_frozen_original(self):
        revision = self.accept(readings(('A', 5), ('A', 7)))
        first = close_batch(self.source, revision)
        original = issue_report(self.source, 'R7', first.result_id)
        corrected = self.accept(readings(('A', 5), ('A', 8)), parent_id=revision)
        second = close_batch(self.source, corrected)
        self.reopen_source()
        recalled = issue_report(self.source, 'R7', first.result_id)
        self.assertEqual(recalled.envelope, original.envelope)
        self.assertEqual(recalled.envelope.original_id,
                         hashlib.sha256(recalled.envelope.body).hexdigest())
        self.assertEqual([row['ml'] for row in json.loads(recalled.envelope.body)['rows']], ['5', '7'])
        latest = issue_report(self.source, 'R8', second.result_id)
        self.assertEqual(json.loads(latest.envelope.body)['total'], {'row_count': 2, 'ml': '13'})

    def test_confirmed_reissue(self):
        issued = self.issue()
        posted = self.stage_and_post(issued)
        confirmed = confirm_receipt(self.source, 'R7', posted.receipt.to_payload())
        self.assertEqual(confirm_receipt(self.source, 'R7', posted.receipt.to_payload()), confirmed)
        repeated = issue_report(self.source, 'R7', issued.result_id)
        self.assertEqual((repeated.envelope, repeated.confirmation, repeated.status),
                         (issued.envelope, confirmed, 'confirmed'))
        self.reopen_source()
        self.initialize_source()
        self.assertEqual(get_issued_report(self.source, 'R7'), repeated)

    def test_missing_result(self):
        with self.assertRaises(KeyError):
            issue_report(self.source, 'R7', 99)
        self.assertIsNone(get_issued_report(self.source, 'R7'))
