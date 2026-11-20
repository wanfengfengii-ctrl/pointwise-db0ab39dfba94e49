from intake.batches import accept_batch
from intake.outlets import find_outlet, register_outlet
from intake.revisions import get_revision
from tests.support import DAY, PLANT, WarehouseCase, readings


class TestIntake(WarehouseCase):
    def test_reject_unknown_outlet(self):
        register_outlet(self.source, '另一厂区', 'Z')
        with self.assertRaisesRegex(ValueError, 'unknown outlet'):
            self.accept(readings(('A', 5), ('Z', 7)))
        for table in ('batches', 'revisions', 'revision_rows'):
            with self.subTest(table=table):
                self.assertEqual(self.source.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        revision_id = self.accept(readings(('A', 5)))
        self.assertEqual(get_revision(self.source, revision_id).rows[0].ml, 5)

    def test_invalid_integer(self):
        invalid = [None, True, False, 7.0, '7']
        items = [{'outlet': 'A', 'ml': value} for value in invalid]
        items += [{'outlet': 'A'}, {'ml': 7}, None]
        for item in items:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.accept([{'outlet': 'A', 'ml': 5}, item])
        with self.assertRaises(ValueError):
            self.accept({})
        self.assertEqual(self.source.execute('SELECT count(*) FROM batches').fetchone()[0], 0)
        self.assertEqual(self.source.execute('SELECT count(*) FROM revisions').fetchone()[0], 0)

    def test_parent_scope(self):
        first = self.accept(readings(('A', 5)))
        sibling = self.accept(readings(('A', 7)), batch='different')
        other_day = accept_batch(self.source, PLANT, '2026-11-19', 'daily', readings(('A', 8)))
        register_outlet(self.source, '另一厂区', 'A')
        other_plant = accept_batch(self.source, '另一厂区', DAY, 'daily', readings(('A', 10)))
        for parent_id in (None, sibling, other_day, other_plant, 999):
            with self.subTest(parent_id=parent_id), self.assertRaisesRegex(ValueError, 'parent revision'):
                self.accept(readings(('A', 6)), parent_id=parent_id)
        self.assertEqual(self.source.execute('SELECT count(*) FROM revisions').fetchone()[0], 4)
        self.assertEqual(get_revision(self.source, sibling).rows[0].ml, 7)
        corrected = get_revision(self.source, self.accept(readings(('A', 6)), parent_id=first))
        self.assertEqual((corrected.parent_id, corrected.rows[0].ml), (first, 6))
        self.assertEqual(get_revision(self.source, first).rows[0].ml, 5)

    def test_initialization_preserves_changes(self):
        register_outlet(self.source, PLANT, 'C')
        first = self.accept(readings(('C', 25)))
        second = self.accept(readings(('C', 27)), parent_id=first)
        self.reopen_source()
        self.initialize_source()
        self.initialize_source()
        self.assertTrue(find_outlet(self.source, PLANT, 'C'))
        self.assertEqual(get_revision(self.source, first).rows[0].ml, 25)
        self.assertEqual(get_revision(self.source, second).parent_id, first)
        self.assertEqual(get_revision(self.source, second).rows[0].ml, 27)
        third = self.accept(readings(('C', 28)), parent_id=second)
        self.assertEqual(get_revision(self.source, third).parent_id, second)
