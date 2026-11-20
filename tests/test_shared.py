import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Barrier

from closeout.jobs import close_batch
from closeout.results import get_result
from dispatch.acknowledgments import confirm_receipt
from dispatch.envelope import ReportEnvelope
from dispatch.issuance import issue_report
from dispatch.outbox import get_issued_report
from intake.batches import accept_shared_batch
from intake.outlets import register_outlet
from intake.revisions import get_shared_revision
from posting.ledger import get_ledger_totals, post_report
from posting.receipts import get_receipt
from posting.receiver_store import open_receiver, receiver_write
from posting.staging import stage_report
from review.reports import audit_report
from tests.support import DAY, PLANT, CommitFailureConnection, WarehouseCase, edit_body

SAMPLE = json.loads((Path(__file__).resolve().parent.parent / 'samples'
                     / 'shared_day.json').read_text(encoding='utf-8'))


def case(name):
    return SAMPLE['cases'][name]


def _compose(report_id, *, plant=PLANT, day=DAY, batch='shared', revision_id=1, result_id=1,
             slots, bounds, allocations):
    """Build a V2 body by hand so alternate-but-legal splits can be delivered."""
    rows = [{'ordinal': index, 'outlet': outlet, 'ml': str(ml)}
            for index, (_, outlet, ml) in enumerate(allocations, start=1)]
    per_outlet = {}
    for _, outlet, ml in allocations:
        count, total = per_outlet.get(outlet, (0, 0))
        per_outlet[outlet] = count + 1, total + ml
    payload = {
        'version': 2, 'report_id': report_id, 'result_id': result_id, 'plant': plant,
        'day': day, 'batch': batch, 'revision_id': revision_id,
        'slots': [{'ordinal': index, 'ml': str(slot['ml']), 'outlets': sorted(slot['outlets'])}
                  for index, slot in enumerate(slots, start=1)],
        'bounds': [{'outlet': outlet, 'min_ml': str(limits['min_ml']),
                    'max_ml': str(limits['max_ml'])}
                   for outlet, limits in sorted(bounds.items())],
        'allocations': [{'ordinal': ordinal, 'outlet': outlet, 'ml': str(ml)}
                        for ordinal, outlet, ml in allocations],
        'rows': rows,
        'outlets': [{'outlet': outlet, 'row_count': count, 'ml': str(total)}
                    for outlet, (count, total) in sorted(per_outlet.items())],
        'total': {'row_count': len(rows), 'ml': str(sum(ml for _, _, ml in allocations))},
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    return ReportEnvelope(report_id, hashlib.sha256(body).hexdigest(), body)


class TestSampleCases(WarehouseCase):
    def test_sample_expected_matches(self):
        for name, spec in SAMPLE['cases'].items():
            with self.subTest(case=name):
                revision_id = accept_shared_batch(self.source, PLANT, DAY, name,
                                                  spec['slots'], spec['bounds'])
                if spec.get('expected', 'missing') is None:
                    with self.assertRaises(ValueError):
                        close_batch(self.source, revision_id)
                    continue
                result = close_batch(self.source, revision_id)
                ordinal = 0
                audit_allocations = []
                for slot in result.shared.slots:
                    items = []
                    for outlet, ml in slot.allocation:
                        ordinal += 1
                        items.append((ordinal, outlet, ml))
                    audit_allocations.append((slot.ordinal, slot.ml, tuple(items)))
                audit_allocations = tuple(audit_allocations)
                if 'expected' in spec:
                    expected = tuple(
                        (ordinal, ml, tuple(tuple(item) for item in items))
                        for ordinal, ml, items in spec['expected'])
                    self.assertEqual(audit_allocations, expected)
                else:
                    def normalize(entry):
                        ordinal, ml, items = entry
                        return [ordinal, ml, [list(item) for item in items]]
                    self.assertIn([normalize(slot) for slot in audit_allocations],
                                  [[normalize(slot) for slot in example]
                                   for example in spec['examples']])


class TestSharedIntake(WarehouseCase):
    def _accept(self, name, *, batch):
        spec = case(name)
        return accept_shared_batch(self.source, PLANT, DAY, batch, spec['slots'], spec['bounds'])

    def test_forced_allocation(self):
        revision_id = self._accept('forced', batch='forced')
        revision = get_shared_revision(self.source, revision_id)
        self.assertEqual([(s.ordinal, s.ml, s.outlets) for s in revision.slots],
                         [(1, 5, ('A', 'B')), (2, 5, ('A',))])
        result = close_batch(self.source, revision_id)
        self.assertEqual([(s.ordinal, s.allocation) for s in result.shared.slots],
                         [(1, (('B', 5),)), (2, (('A', 5),))])
        self.assertEqual([(r.ordinal, r.outlet, r.ml) for r in result.revision.rows],
                         [(1, 'B', 5), (2, 'A', 5)])
        self.assertEqual((result.totals.row_count, result.totals.ml), (2, 10))
        self.assertEqual(close_batch(self.source, revision_id), result)
        self.reopen_source()
        reloaded = get_result(self.source, result.result_id)
        self.assertEqual(reloaded.shared, result.shared)

    def test_equal_and_zero_slots(self):
        revision_id = self._accept('equal', batch='equal')
        result = close_batch(self.source, revision_id)
        self.assertEqual([(s.ordinal, s.ml, s.allocation) for s in result.shared.slots],
                         [(1, 5, (('A', 5),)), (2, 0, ()), (3, 5, (('A', 5),))])

    def test_lower_split(self):
        result = close_batch(self.source, self._accept('lower_split', batch='lower'))
        self.assertEqual([(s.ordinal, s.allocation) for s in result.shared.slots],
                         [(1, (('A', 2), ('B', 3)))])

    def test_infeasible_keeps_revision(self):
        for name, batch in (('upper_infeasible', 'upper'), ('lower_infeasible', 'lower')):
            with self.subTest(name=name):
                revision_id = self._accept(name, batch=batch)
                with self.assertRaisesRegex(ValueError, 'no allocation'):
                    close_batch(self.source, revision_id)
                self.assertIsNotNone(get_shared_revision(self.source, revision_id))
                self.assertEqual(self.source.execute('SELECT count(*) FROM closeouts').fetchone()[0], 0)
        self.assertEqual(get_shared_revision(self.source, revision_id).slots[0].ml, 5)

    def test_reject_invalid_material(self):
        bad_slots = [
            [{'ml': True, 'outlets': ['A']}],
            [{'ml': -1, 'outlets': ['A']}],
            [{'ml': 5, 'outlets': ['A', 'A']}],
            [{'ml': 5, 'outlets': ['A'], 'extra': 1}],
            [{'ml': 5}],
            'not-a-sequence',
        ]
        for slots in bad_slots:
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                accept_shared_batch(self.source, PLANT, DAY, 'reject', slots,
                                    {'A': {'min_ml': 0, 'max_ml': 10}})
        with self.assertRaises(ValueError):
            accept_shared_batch(self.source, PLANT, DAY, 'reject',
                                [{'ml': 5, 'outlets': ['Q']}], {'A': {'min_ml': 0, 'max_ml': 5}})
        with self.assertRaises(ValueError):
            accept_shared_batch(self.source, PLANT, DAY, 'reject',
                                [{'ml': 5, 'outlets': ['A']}], {})
        with self.assertRaises(ValueError):
            accept_shared_batch(self.source, PLANT, DAY, 'reject',
                                [{'ml': 5, 'outlets': ['A']}],
                                {'A': {'min_ml': 6, 'max_ml': 5}})
        register_outlet(self.source, '另一厂区', 'Z')
        with self.assertRaises(ValueError):
            accept_shared_batch(self.source, PLANT, DAY, 'reject',
                                [{'ml': 5, 'outlets': ['A']}],
                                {'A': {'min_ml': 0, 'max_ml': 5},
                                 'Z': {'min_ml': 0, 'max_ml': 5}})
        self.assertEqual(self.source.execute('SELECT count(*) FROM revisions').fetchone()[0], 0)
        self.assertEqual(self.source.execute('SELECT count(*) FROM batches').fetchone()[0], 0)

    def test_parent_scope(self):
        first = self._accept('forced', batch='parent')
        second = accept_shared_batch(self.source, PLANT, DAY, 'parent', case('equal')['slots'],
                                     case('equal')['bounds'], parent_id=first)
        revision = get_shared_revision(self.source, second)
        self.assertEqual((revision.parent_id, revision.slots[1].ml), (first, 0))
        with self.assertRaisesRegex(ValueError, 'parent revision'):
            accept_shared_batch(self.source, PLANT, DAY, 'other', case('equal')['slots'],
                                case('equal')['bounds'], parent_id=first)


class TestSharedPosting(WarehouseCase):
    def _issue(self, name='forced', *, report_id='S1', batch='forced'):
        spec = case(name)
        revision_id = accept_shared_batch(self.source, PLANT, DAY, batch, spec['slots'],
                                          spec['bounds'])
        result = close_batch(self.source, revision_id)
        return issue_report(self.source, report_id, result.result_id)

    def test_full_cycle_and_reopen(self):
        issued = self._issue()
        self.assertEqual(json.loads(issued.envelope.body)['version'], 2)
        stage_id = stage_report(self.receiver, issued.envelope)
        self.assertEqual((get_ledger_totals(self.receiver).row_count,
                          get_ledger_totals(self.receiver).ml), (0, 0))
        posted = post_report(self.receiver, stage_id)
        self.assertEqual([(r.ordinal, r.outlet, r.ml) for r in posted.rows],
                         [(1, 'B', 5), (2, 'A', 5)])
        self.source.close()
        self.reopen_receiver()
        audit = audit_report(self.receiver, 'S1')
        self.assertEqual(audit.slot_allocations,
                         ((1, 5, ((1, 'B', 5),)), (2, 5, ((2, 'A', 5),))))
        self.assertEqual(audit.discrepancies, ())
        self.assertEqual(audit.receipt, posted.receipt)

    def test_zero_slot_audit(self):
        issued = self._issue('equal', report_id='S2', batch='equal')
        post_report(self.receiver, stage_report(self.receiver, issued.envelope))
        audit = audit_report(self.receiver, 'S2')
        self.assertEqual(audit.slot_allocations,
                         ((1, 5, ((1, 'A', 5),)), (2, 0, ()), (3, 5, ((2, 'A', 5),))))
        self.assertEqual(audit.discrepancies, ())

    def test_both_legal_solutions_accepted(self):
        example_one, example_two = case('multiple')['examples']
        for report_id, allocation in (('M1', example_one), ('M2', example_two)):
            flat = [(ordinal, outlet, ml) for ordinal, _, items in allocation
                    for _, outlet, ml in items]
            envelope = _compose(report_id, batch=report_id, slots=case('multiple')['slots'],
                                bounds=case('multiple')['bounds'], allocations=flat)
            post_report(self.receiver, stage_report(self.receiver, envelope))
            audit = audit_report(self.receiver, report_id)
            self.assertEqual(audit.discrepancies, ())
            self.assertEqual([(r.ordinal, r.outlet, r.ml) for r in audit.ledger_rows], [
                (index, outlet, ml) for index, (_, outlet, ml) in enumerate(flat, start=1)])
            self.assertEqual(audit.slot_allocations[0][1], 0)
            self.assertEqual(audit.slot_allocations[0][2], ())

    def test_reject_attribution_wrong_total_right(self):
        envelope = self._issue().envelope

        def swap_slots(data):
            data['allocations'] = [
                {'ordinal': 1, 'outlet': 'A', 'ml': '5'},
                {'ordinal': 2, 'outlet': 'B', 'ml': '5'},
            ]

        def swap_rows(data):
            data['rows'][0]['outlet'] = 'A'
            data['rows'][1]['outlet'] = 'B'

        with self.assertRaisesRegex(ValueError, 'does not permit'):
            stage_report(self.receiver, edit_body(envelope, swap_slots))
        with self.assertRaisesRegex(ValueError, 'differ from the allocation evidence'):
            stage_report(self.receiver, edit_body(envelope, swap_rows))
        self.assertEqual(get_ledger_totals(self.receiver).row_count, 0)
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM staged_reports').fetchone()[0], 0)

    def test_reject_conservation_and_bounds(self):
        envelope = self._issue().envelope

        def break_conservation(data):
            data['allocations'][0]['ml'] = '4'

        def break_bounds(data):
            data['bounds'][1]['min_ml'] = '6'

        for edit, pattern in ((break_conservation, 'conserve'), (break_bounds, 'min_ml')):
            with self.subTest(edit=edit), self.assertRaisesRegex(ValueError, pattern):
                stage_report(self.receiver, edit_body(envelope, edit))
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM staged_reports').fetchone()[0], 0)

    def test_wrong_digest_rejected(self):
        envelope = self._issue().envelope
        payload = json.loads(envelope.body)
        payload['allocations'][0]['outlet'] = 'A'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        with self.assertRaises(ValueError):
            stage_report(self.receiver, replace(envelope, body=body))
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM staged_reports').fetchone()[0], 0)

    def test_stage_replay_same_original(self):
        envelope = self._issue().envelope
        stage_id = stage_report(self.receiver, envelope)
        self.reopen_receiver()
        self.assertEqual(stage_report(self.receiver, envelope), stage_id)
        self.assertEqual(get_ledger_totals(self.receiver).row_count, 0)

    def test_same_number_different_original(self):
        first = self._issue(report_id='S1', batch='one')
        other = _compose('S1', batch='two', slots=case('equal')['slots'],
                         bounds=case('equal')['bounds'],
                         allocations=[(1, 'A', 5), (3, 'A', 5)])
        stage_id = stage_report(self.receiver, first.envelope)
        with self.assertRaisesRegex(ValueError, 'different original'):
            stage_report(self.receiver, other)
        self.assertEqual(stage_report(self.receiver, first.envelope), stage_id)

    def test_receipt_replay_and_confirm(self):
        issued = self._issue()
        stage_id = stage_report(self.receiver, issued.envelope)
        first = post_report(self.receiver, stage_id)
        self.assertEqual(post_report(self.receiver, stage_id), first)
        self.assertEqual(get_receipt(self.receiver, 'S1'), first.receipt)
        confirmed = confirm_receipt(self.source, 'S1', first.receipt.to_payload())
        self.assertEqual(get_issued_report(self.source, 'S1').confirmation, confirmed)
        self.source.close()
        self.reopen_receiver()
        self.assertEqual(get_receipt(self.receiver, 'S1'), first.receipt)

    def test_commit_failure_retry(self):
        issued = self._issue()
        stage_id = stage_report(self.receiver, issued.envelope)
        self.reopen_receiver(factory=CommitFailureConnection)

        def observe(writer):
            self.assertEqual(get_receipt(writer, 'S1').original_id, issued.envelope.original_id)
            with closing(open_receiver(self.receiver_path)) as observer:
                self.assertEqual(get_ledger_totals(observer).row_count, 0)

        self.receiver.arm(observe)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            post_report(self.receiver, stage_id)
        self.assertIsNone(get_receipt(self.receiver, 'S1'))
        self.assertEqual(get_ledger_totals(self.receiver).row_count, 0)
        posted = post_report(self.receiver, stage_id)
        self.assertEqual([(r.ordinal, r.outlet, r.ml) for r in posted.rows],
                         [(1, 'B', 5), (2, 'A', 5)])

    def test_concurrent_replay(self):
        stage_id = stage_report(self.receiver, self._issue().envelope)
        barrier = Barrier(2, timeout=5)

        def replay():
            with closing(open_receiver(self.receiver_path)) as receiver:
                barrier.wait()
                return post_report(receiver, stage_id)

        with ThreadPoolExecutor(max_workers=2) as workers:
            tasks = [workers.submit(replay) for _ in range(2)]
            results = [task.result(timeout=15) for task in tasks]
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.receiver.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_audit_before_post(self):
        issued = self._issue()
        with self.assertRaises(KeyError):
            audit_report(self.receiver, 'S1')
        stage_report(self.receiver, issued.envelope)
        with self.assertRaises(KeyError):
            audit_report(self.receiver, 'S1')

    def test_ledger_tamper_audit(self):
        issued = self._issue()
        post_report(self.receiver, stage_report(self.receiver, issued.envelope))
        with receiver_write(self.receiver):
            self.receiver.execute("UPDATE ledger_rows SET ml = '4' WHERE report_id = 'S1' AND ordinal = 1")
        self.source.close()
        audit = audit_report(self.receiver, 'S1')
        self.assertEqual(audit.original_rows[0].ml, 5)
        self.assertEqual(audit.ledger_rows[0].ml, 4)
        self.assertTrue(audit.discrepancies)
