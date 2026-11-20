from closeout.jobs import close_batch
from closeout.results import get_result
from tests.support import WarehouseCase, readings


class TestCloseout(WarehouseCase):
    def test_totals(self):
        revision_id = self.accept(readings(('A', 5), ('A', 7), ('B', 3)))
        result = close_batch(self.source, revision_id)
        self.assertEqual((result.totals.row_count, result.totals.ml), (3, 15))
        self.assertEqual([(x.outlet, x.row_count, x.ml) for x in result.totals.outlets],
                         [('A', 2, 12), ('B', 1, 3)])
        self.assertEqual(close_batch(self.source, revision_id), result)

    def test_frozen_revision(self):
        first = self.accept(readings(('A', 5), ('A', 7), ('B', 3)))
        original = close_batch(self.source, first)
        second = self.accept(readings(('A', 5), ('A', 8), ('B', 3)), parent_id=first)
        corrected = close_batch(self.source, second)
        self.assertNotEqual(original.result_id, corrected.result_id)
        self.reopen_source()
        old = get_result(self.source, original.result_id)
        new = get_result(self.source, corrected.result_id)
        self.assertEqual((old.revision.revision_id, new.revision.revision_id), (first, second))
        self.assertEqual((old.totals.ml, new.totals.ml), (15, 16))
        self.assertEqual([row.ml for row in old.revision.rows], [5, 7, 3])
        self.assertEqual([row.ml for row in new.revision.rows], [5, 8, 3])

    def test_duplicate_rows(self):
        revision_id = self.accept(readings(('A', 5), ('B', 3), ('A', 5)))
        result = close_batch(self.source, revision_id)
        fixed = get_result(self.source, result.result_id)
        self.assertEqual([(x.ordinal, x.outlet, x.ml) for x in fixed.revision.rows],
                         [(1, 'A', 5), (2, 'B', 3), (3, 'A', 5)])
        self.assertEqual([(x.outlet, x.row_count, x.ml) for x in fixed.totals.outlets],
                         [('A', 2, 10), ('B', 1, 3)])
        self.assertEqual((fixed.totals.row_count, fixed.totals.ml), (3, 13))

    def test_empty_batch(self):
        result = close_batch(self.source, self.accept([]))
        fixed = get_result(self.source, result.result_id)
        self.assertEqual(fixed.revision.rows, ())
        self.assertEqual(fixed.totals.outlets, ())
        self.assertEqual((fixed.totals.row_count, fixed.totals.ml), (0, 0))

    def test_unknown_revision(self):
        with self.assertRaises(KeyError):
            close_batch(self.source, 99)
        self.assertEqual(self.source.execute('SELECT count(*) FROM closeouts').fetchone()[0], 0)
