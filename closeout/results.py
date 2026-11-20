from dataclasses import dataclass
from decimal import Decimal

from closeout.shared import SharedCloseout
from closeout.totals import CloseoutTotals, OutletTotal
from intake.revisions import (BatchRevision, RevisionRow, get_shared_revision,
                              is_shared_revision)
from intake.source_store import require_id, source_write


@dataclass(frozen=True)
class CloseoutResult:
    result_id: int
    revision: BatchRevision
    totals: CloseoutTotals
    shared: SharedCloseout | None = None


def initialize_closeout(db):
    with source_write(db):
        db.execute('''CREATE TABLE IF NOT EXISTS closeouts (
            result_id INTEGER PRIMARY KEY,
            revision_id INTEGER NOT NULL UNIQUE REFERENCES revisions(revision_id),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            ml TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_rows (
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            outlet TEXT NOT NULL,
            ml TEXT NOT NULL,
            PRIMARY KEY (result_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_outlets (
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            outlet TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count > 0),
            ml TEXT NOT NULL,
            PRIMARY KEY (result_id, outlet)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_slots (
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            ordinal INTEGER NOT NULL CHECK (ordinal > 0),
            ml TEXT NOT NULL,
            PRIMARY KEY (result_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_slot_permits (
            result_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            outlet TEXT NOT NULL,
            PRIMARY KEY (result_id, ordinal, outlet),
            FOREIGN KEY (result_id, ordinal) REFERENCES closeout_slots(result_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_slot_allocations (
            result_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            outlet TEXT NOT NULL,
            ml TEXT NOT NULL,
            PRIMARY KEY (result_id, ordinal, outlet),
            FOREIGN KEY (result_id, ordinal) REFERENCES closeout_slots(result_id, ordinal)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS closeout_bounds (
            result_id INTEGER NOT NULL REFERENCES closeouts(result_id),
            outlet TEXT NOT NULL,
            min_ml TEXT NOT NULL,
            max_ml TEXT NOT NULL,
            PRIMARY KEY (result_id, outlet)
        )''')


def store_result(db, revision, totals):
    """Freeze the calculation inside the caller's source transaction."""
    cursor = db.execute('''INSERT INTO closeouts (revision_id, row_count, ml)
        VALUES (?, ?, ?)''', (revision.revision_id, totals.row_count, str(Decimal(totals.ml))))
    result_id = cursor.lastrowid
    db.executemany('''INSERT INTO closeout_rows (result_id, ordinal, outlet, ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, row.ordinal, row.outlet, str(Decimal(row.ml))) for row in revision.rows))
    db.executemany('''INSERT INTO closeout_outlets (result_id, outlet, row_count, ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, item.outlet, item.row_count, str(Decimal(item.ml))) for item in totals.outlets))
    return CloseoutResult(result_id, revision, totals)


def store_shared_result(db, revision, shared):
    """Freeze a shared allocation inside the caller's source transaction."""
    totals = shared.totals
    cursor = db.execute('''INSERT INTO closeouts (revision_id, row_count, ml)
        VALUES (?, ?, ?)''', (revision.revision_id, totals.row_count, str(Decimal(totals.ml))))
    result_id = cursor.lastrowid
    db.executemany('''INSERT INTO closeout_rows (result_id, ordinal, outlet, ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, row.ordinal, row.outlet, str(Decimal(row.ml))) for row in shared.rows))
    db.executemany('''INSERT INTO closeout_outlets (result_id, outlet, row_count, ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, item.outlet, item.row_count, str(Decimal(item.ml))) for item in totals.outlets))
    db.executemany('''INSERT INTO closeout_slots (result_id, ordinal, ml) VALUES (?, ?, ?)''',
                   ((result_id, slot.ordinal, str(Decimal(slot.ml))) for slot in shared.slots))
    db.executemany('''INSERT INTO closeout_slot_permits (result_id, ordinal, outlet)
        VALUES (?, ?, ?)''',
        ((result_id, slot.ordinal, outlet) for slot in shared.slots for outlet in slot.outlets))
    db.executemany('''INSERT INTO closeout_slot_allocations
        (result_id, ordinal, outlet, ml) VALUES (?, ?, ?, ?)''',
        ((result_id, slot.ordinal, outlet, str(Decimal(ml)))
         for slot in shared.slots for outlet, ml in slot.allocation))
    db.executemany('''INSERT INTO closeout_bounds (result_id, outlet, min_ml, max_ml)
        VALUES (?, ?, ?, ?)''',
        ((result_id, outlet, str(Decimal(low)), str(Decimal(high)))
         for outlet, low, high in shared.bounds))
    return CloseoutResult(result_id, revision, totals, shared)


def _load_shared(db, result_id):
    from closeout.shared import AllocatedSlot
    slots = []
    for ordinal, ml in db.execute('''SELECT ordinal, ml FROM closeout_slots
            WHERE result_id = ? ORDER BY ordinal''', (result_id,)):
        permits = tuple(row[0] for row in db.execute('''SELECT outlet
            FROM closeout_slot_permits WHERE result_id = ? AND ordinal = ?
            ORDER BY outlet''', (result_id, ordinal)))
        parts = tuple((outlet, int(Decimal(amount))) for outlet, amount in db.execute(
            '''SELECT outlet, ml FROM closeout_slot_allocations
                WHERE result_id = ? AND ordinal = ? ORDER BY outlet''', (result_id, ordinal)))
        slots.append(AllocatedSlot(ordinal, int(Decimal(ml)), permits, parts))
    bounds = tuple((outlet, int(Decimal(low)), int(Decimal(high))) for outlet, low, high in
                   db.execute('''SELECT outlet, min_ml, max_ml FROM closeout_bounds
                       WHERE result_id = ? ORDER BY outlet''', (result_id,)))
    return SharedCloseout(tuple(slots), bounds)


def get_result(db, result_id):
    require_id(result_id, 'result_id')
    header = db.execute('''SELECT r.revision_id, r.plant, r.day, r.batch, r.parent_id,
            c.row_count, c.ml
        FROM closeouts AS c JOIN revisions AS r ON r.revision_id = c.revision_id
        WHERE c.result_id = ?''', (result_id,)).fetchone()
    if header is None:
        return None
    if is_shared_revision(db, header['revision_id']):
        shared_revision = get_shared_revision(db, header['revision_id'])
        slots = shared_revision.slots
        rows = tuple(RevisionRow(ordinal, outlet, int(Decimal(ml))) for ordinal, outlet, ml in
                     db.execute('''SELECT ordinal, outlet, ml FROM closeout_rows
                         WHERE result_id = ? ORDER BY ordinal''', (result_id,)))
        revision = BatchRevision(*header[:5], rows)
        records = db.execute('''SELECT outlet, row_count, ml FROM closeout_outlets
            WHERE result_id = ? ORDER BY outlet''', (result_id,))
        outlets = tuple(OutletTotal(outlet, count, int(Decimal(ml)))
                        for outlet, count, ml in records)
        totals = CloseoutTotals(outlets, header['row_count'], int(Decimal(header['ml'])))
        return CloseoutResult(result_id, revision, totals, _load_shared(db, result_id))
    records = db.execute('''SELECT ordinal, outlet, ml FROM closeout_rows
        WHERE result_id = ? ORDER BY ordinal''', (result_id,))
    rows = tuple(RevisionRow(ordinal, outlet, int(Decimal(ml)))
                 for ordinal, outlet, ml in records)
    revision = BatchRevision(*header[:5], rows)
    records = db.execute('''SELECT outlet, row_count, ml FROM closeout_outlets
        WHERE result_id = ? ORDER BY outlet''', (result_id,))
    outlets = tuple(OutletTotal(outlet, count, int(Decimal(ml)))
                    for outlet, count, ml in records)
    totals = CloseoutTotals(outlets, header['row_count'], int(Decimal(header['ml'])))
    return CloseoutResult(result_id, revision, totals)


def find_result_for_revision(db, revision_id):
    require_id(revision_id, 'revision_id')
    record = db.execute('SELECT result_id FROM closeouts WHERE revision_id = ?',
                        (revision_id,)).fetchone()
    return None if record is None else get_result(db, record['result_id'])
